# voice_agent_with_transfer.py
import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
from typing import Literal, Optional, Any

# LiveKit imports
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    metrics,
    RoomInputOptions,
    BackgroundAudioPlayer,
    PlayHandle,
    RunContext,
)
from livekit.agents.llm import function_tool
from livekit.plugins import (
    cartesia,
    openai,
    deepgram,
    noise_cancellation,
    silero,
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv(dotenv_path=".env.local")
logger = logging.getLogger("voice-agent")
logger.setLevel(logging.DEBUG)

# Make `agent/src/` importable for prompt templates and other modules.
_SRC_PATH = os.path.join(os.path.dirname(__file__), "..", "src")
_SRC_PATH = os.path.normpath(_SRC_PATH)
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)

# user project prompt utilities (your existing prompt template)
from core.prompts.template import PromptConfig, render_prompt  # noqa: E402

# required env variables
SIP_TRUNK_ID = os.getenv("LIVEKIT_SIP_OUTBOUND_TRUNK")  # e.g. "ST_abcxyz"
SUPERVISOR_PHONE_NUMBER = os.getenv("LIVEKIT_SUPERVISOR_PHONE_NUMBER")  # e.g. "+12003004000"
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
# identity used for the SIP participant created for the supervisor
_SUPERVISOR_IDENTITY = "supervisor-sip"

# small enums for session states
SupervisorStatus = Literal["inactive", "summarizing", "merged", "failed"]
CustomerStatus = Literal["active", "escalated", "passive"]


class SessionManager:
    """
    Orchestrates warm transfer:
      - puts the caller on hold
      - creates a consultation room
      - spawns/join a Transfer/Summary agent
      - dials supervisor via SIP to the consult room
      - moves the supervisor to caller room (merge)
      - recovers on failures
    """

    def __init__(
        self,
        *,
        ctx: JobContext,
        customer_room: rtc.Room,
        customer_session: AgentSession,
        supervisor_contact: str,
        lkapi: api.LiveKitAPI,
    ):
        self.ctx = ctx
        self.customer_room = customer_room
        self.customer_session = customer_session
        self.supervisor_contact = supervisor_contact
        self.lkapi = lkapi

        self.background_audio = BackgroundAudioPlayer()
        self.hold_audio_handle: Optional[PlayHandle] = None

        self.supervisor_room: Optional[rtc.Room] = None
        self.supervisor_session: Optional[AgentSession] = None

        self.customer_status: CustomerStatus = "active"
        self.supervisor_status: SupervisorStatus = "inactive"

    async def start(self) -> None:
        # start the background audio system (used for hold music)
        await self.background_audio.start(room=self.customer_room, agent_session=self.customer_session)

    def start_hold(self):
        # disable audio to/from caller and play hold music
        logger.debug("putting customer on hold (disable audio + play hold music)")
        self.customer_session.input.set_audio_enabled(False)
        self.customer_session.output.set_audio_enabled(False)
        # play hold music file from working dir (ensure file exists)
        self.hold_audio_handle = self.background_audio.play(
            api=None,  # pass AudioConfig-like object as in your runtime; to avoid strong dependency we'll use the filename call below if available
            # In many LiveKit examples: AudioConfig("hold_music.mp3", volume=0.8)
        )
        # If play wrapper above isn't available in your agent version, replace with:
        # self.hold_audio_handle = self.background_audio.play(AudioConfig("hold_music.mp3", volume=0.8), loop=True)

    def stop_hold(self):
        logger.debug("stopping hold")
        if self.hold_audio_handle:
            try:
                self.hold_audio_handle.stop()
            except Exception:
                logger.exception("error stopping hold audio")
            self.hold_audio_handle = None

        # re-enable audio for the customer session
        try:
            self.customer_session.input.set_audio_enabled(True)
            self.customer_session.output.set_audio_enabled(True)
        except Exception:
            logger.exception("error re-enabling customer audio")

    async def start_transfer(self):
        """
        Kick off the transfer: create consult room, start a SupervisorAgent,
        dial the supervisor via SIP into the consult room.
        """
        if self.customer_status != "active":
            logger.info("transfer already in progress or customer not active")
            return

        if not SIP_TRUNK_ID or not self.supervisor_contact:
            logger.error("SIP_TRUNK_ID or SUPERVISOR_PHONE_NUMBER not configured")
            await self.customer_session.say("Sorry, transfer is unavailable right now.")
            return

        self.customer_status = "escalated"
        # hold the customer
        self.start_hold()
        await self.customer_session.say("Please hold while I connect you to a human agent.")

        try:
            # create consultation room name based on customer room
            consult_room_name = f"{self.customer_room.name}-consult"
            self.supervisor_room = rtc.Room()

            # generate token for summary agent to join consult room
            token = (
                api.AccessToken()
                .with_identity("summary-agent")
                .with_grants(
                    api.VideoGrants(
                        room_join=True,
                        room=consult_room_name,
                        can_update_own_metadata=True,
                        can_publish=True,
                        can_subscribe=True,
                    )
                )
            )
            # connect the internal supervisor room (agent side)
            logger.info("connecting summary agent to consult room", extra={"room": consult_room_name})
            await self.supervisor_room.connect(LIVEKIT_URL, token.to_jwt())
            self.supervisor_room.on("disconnected", lambda reason: asyncio.create_task(self.set_supervisor_failed()))

            # create supervisor AgentSession
            self.supervisor_session = AgentSession(
                vad=silero.VAD.load(),
                llm=_create_llm(),
                stt=_create_stt(),
                tts=_create_tts(),
                turn_detection=MultilingualModel(),
            )

            # Build SupervisorAgent with conversation history
            supervisor_agent = SupervisorAgent(prev_ctx=self.customer_session.history)
            supervisor_agent.session_manager = self

            await self.supervisor_session.start(
                agent=supervisor_agent,
                room=self.supervisor_room,
                room_input_options=RoomInputOptions(close_on_disconnect=True),
            )

            # Dial out to supervisor via SIP into the consult room
            logger.info("dialing supervisor via SIP", extra={"to": self.supervisor_contact})
            await self.lkapi.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    sip_trunk_id=SIP_TRUNK_ID,
                    sip_call_to=self.supervisor_contact,
                    room_name=consult_room_name,
                    participant_identity=_SUPERVISOR_IDENTITY,
                    wait_until_answered=True,
                )
            )

            self.supervisor_status = "summarizing"
            logger.info("supervisor dialed, waiting for summary step")
        except Exception:
            logger.exception("failed to start transfer")
            self.customer_status = "active"
            await self.set_supervisor_failed()

    async def set_supervisor_failed(self):
        # recovery if something goes wrong with the supervisor call
        self.supervisor_status = "failed"
        self.stop_hold()
        try:
            # inform customer
            await self.customer_session.say("Sorry, I couldn't connect you to a supervisor. How else can I help?")
        except Exception:
            logger.exception("error notifying customer about failed transfer")

        if self.supervisor_session:
            try:
                await self.supervisor_session.aclose()
            except Exception:
                logger.exception("error closing supervisor session")
            self.supervisor_session = None

    async def merge_calls(self):
        # move the supervisor SIP participant into the customer's room
        if self.supervisor_status != "summarizing":
            logger.info("merge_calls called but supervisor not in summarizing state")
            return

        if not (self.supervisor_room and self.supervisor_room.name and self.customer_room and self.customer_room.name):
            logger.error("rooms missing for merge")
            await self.set_supervisor_failed()
            return

        try:
            # move participant from consult room into customer room
            await self.lkapi.room.move_participant(
                api.MoveParticipantRequest(
                    room=self.supervisor_room.name,
                    identity=_SUPERVISOR_IDENTITY,
                    destination_room=self.customer_room.name,
                )
            )

            # stop hold and re-enable audio
            self.stop_hold()

            # brief farewell from the agent before leaving
            await self.customer_session.say("You are now connected to a human supervisor. I will leave the line now. Goodbye.")
            # close support agent session (it will disconnect from the room)
            await self.customer_session.aclose()

            # close the supervisor agent session if still active (the supervisor is now in the customer room)
            if self.supervisor_session:
                try:
                    await self.supervisor_session.aclose()
                except Exception:
                    logger.exception("error closing supervisor agent session")
                self.supervisor_session = None

            self.supervisor_status = "merged"
            logger.info("calls merged successfully")
        except Exception:
            logger.exception("could not merge calls")
            await self.set_supervisor_failed()


# ---------- Agents ----------

# create helper LLM/STT/TTS factories used by agents
def _create_llm():
    return openai.LLM(model="gpt-4o-mini")


def _create_stt():
    return deepgram.STT(model="nova-2", language="en")


def _create_tts():
    return cartesia.TTS(model="sonic-2", voice="f8f5f1b2-f02d-4d8e-a40d-fd850a487b3d", speed=0.6)


# Your original Assistant, now augmented with transfer tool
class Assistant(Agent):
    def __init__(self) -> None:
        cfg = PromptConfig(agent_name="Sini")
        system_instructions = render_prompt(cfg)

        super().__init__(
            instructions=system_instructions,
            stt=_create_stt(),
            llm=_create_llm(),
            tts=_create_tts(),
            turn_detection=MultilingualModel(),
        )
        self.session_manager: Optional[SessionManager] = None

    async def on_enter(self):
        # greet politely when user joins
        await self.session.generate_reply(
            instructions="Thank you for calling in Global Horizons Travel, I'm Sini. How can I help you today?",
            allow_interruptions=True,
        )

    @function_tool
    async def transfer_to_human(self, context: RunContext):
        """
        Function tool triggered when the user confirms they want a human.
        The agent should ask for confirmation before calling this.
        """
        logger.info("transfer_to_human tool called")
        # sanity check
        if not self.session_manager:
            logger.error("no session_manager attached to agent when transfer requested")
            await self.session.say("Sorry, transfer is not available right now.")
            return None

        # confirm requires the agent's dialogue; assume caller confirmed already if the tool is invoked
        await self.session.say("Please hold while I connect you to a human agent.")
        await self.session_manager.start_transfer()
        return None


class SupervisorAgent(Agent):
    """
    Agent used inside the consultation room to summarize the conversation for a human supervisor.
    """

    def __init__(self, prev_ctx: Any):
        # build a short set of instructions + conversation history passed in prev_ctx (llm.ChatContext)
        prev_convo = ""
        try:
            context_copy = prev_ctx.copy(
                exclude_empty_message=True, exclude_instructions=True, exclude_function_call=True
            )
            for msg in context_copy.items:
                if msg.role == "user":
                    prev_convo += f"Customer: {msg.text_content}\n"
                else:
                    prev_convo += f"Assistant: {msg.text_content}\n"
        except Exception:
            prev_convo = "(failed to copy conversation history)"

        instructions = (
            "You are a summary agent. Your job is to briefly summarize the customer's issue for a human supervisor. "
            "Start by greeting the supervisor, then provide a short summary of the conversation below, and wait for the supervisor "
            "to confirm they can take the call. When the supervisor says they are ready, call the tool 'connect_to_customer'.\n\n"
            "Conversation history:\n" + prev_convo
        )

        super().__init__(instructions=instructions, llm=_create_llm(), stt=_create_stt(), tts=_create_tts())
        self.prev_ctx = prev_ctx
        self.session_manager: Optional[SessionManager] = None

    async def on_enter(self):
        logger.info("SupervisorAgent on_enter: will summarize to supervisor")
        # the agent will use its instructions to produce the summary automatically (on its generation loop)

    @function_tool
    async def connect_to_customer(self, context: RunContext):
        """
        Called by the supervisor agent when the supervisor agrees to speak to the customer.
        This will move the supervisor participant into the customer room and complete the warm transfer.
        """
        logger.info("SupervisorAgent.connect_to_customer called")
        if not self.session_manager:
            logger.error("no session_manager on supervisor agent")
            await self.session.say("Sorry, something went wrong. I can't connect you automatically.")
            return None

        await self.session.say("Connecting you to the customer now.")
        await self.session_manager.merge_calls()
        return None

    @function_tool
    async def voicemail_detected(self, context: RunContext):
        """
        SupervisorAgent can call this if it detects the dialed line went to voicemail.
        """
        logger.info("voicemail detected while dialing supervisor")
        if self.session_manager:
            await self.session_manager.set_supervisor_failed()


# ---------- Entrypoint & runtime ----------

def prewarm(proc: JobProcess):
    # load and cache VAD model to speed start-up
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # wait for participant (caller)
    participant = await ctx.wait_for_participant()
    logger.info(f"participant connected: {participant.identity}")

    usage_collector = metrics.UsageCollector()

    def on_metrics_collected(agent_metrics: metrics.AgentMetrics):
        metrics.log_metrics(agent_metrics)
        usage_collector.collect(agent_metrics)

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        min_endpointing_delay=0.5,
        max_endpointing_delay=5.0,
    )
    session.on("metrics_collected", on_metrics_collected)

    # create assistant (Sini) and start session
    assistant = Assistant()
    await session.start(
        room=ctx.room,
        agent=assistant,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    # attach the session manager for warm transfer orchestration
    session_manager = SessionManager(
        ctx=ctx,
        customer_room=ctx.room,
        customer_session=session,
        supervisor_contact=SUPERVISOR_PHONE_NUMBER,
        lkapi=ctx.api,
    )
    assistant.session_manager = session_manager

    await session_manager.start()


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
