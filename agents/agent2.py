#STT-> Deepgram and TTS-> servam
import logging

import os
import sys
from dotenv import load_dotenv
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
    
)
from livekit.plugins import (
    openai,
    sarvam,
    noise_cancellation,
    silero,
    deepgram,
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel


load_dotenv(dotenv_path=".env.local")
logger = logging.getLogger("voice-agent")

# Make `agent/src/` importable for prompt templates and other modules.
_SRC_PATH = os.path.join(os.path.dirname(__file__), "..", "src")
_SRC_PATH = os.path.normpath(_SRC_PATH)
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)



class Assistant(Agent):
    def __init__(self) -> None:
        # This project is configured to use Deepgram STT, OpenAI LLM and Cartesia TTS plugins
        # Other great providers exist like Cerebras, ElevenLabs, Groq, Play.ht, Rime, and more
        # Learn more and pick the best one for your app:
        # https://docs.livekit.io/agents/plugins
        # Build system prompt via our configurable template
    

        super().__init__(
                       instructions= """
            You are an AI router setup assistant calling a customer on the phone.

This is a real phone call, so:
- Speak slowly and clearly.
- Use short sentences.
- Do not use bullet points, symbols, or formatting.
- Do not speak more than 2 sentences at a time.
- Always wait for the customer to reply before continuing.

Your job is to guide the customer step by step to set up their WiFi router.
Do NOT skip steps.
Do NOT move ahead unless the customer confirms the current step.

Start the call with a polite greeting and introduction.

CALL FLOW YOU MUST FOLLOW EXACTLY:

STEP 1 – GREETING
Start by saying:
"Hello, this is an automated support call to help you set up your WiFi router.
Is this a good time to continue?"

If the customer says no, busy, or later:
Politely say:
"No problem. We can do this later. Thank you."
Then end the call.

If the customer agrees, continue.

STEP 2 – CONFIRM LOCATION
Ask:
"Are you near your WiFi router right now?"

If no:
Ask them to go near the router and tell you when they are ready.
Do not continue until they confirm they are near the router.

STEP 3 – POWER CHECK
Say:
"Please connect the power cable to the router and turn it on.
Tell me when the lights come on."

Wait for confirmation.

STEP 4 – LED STATUS
Ask:
"What color light do you see on the router right now?"

Handle responses like:
- Green
- Orange
- Red
- Blinking
- No light

If GREEN:
Say:
"Great. That means the router has power."

If ORANGE or RED:
Say:
"That is okay. We will fix it step by step."

If NO LIGHT:
Ask them to check the power cable and switch again.

Do not proceed until a light is visible.

STEP 5 – INTERNET CABLE
Say:
"Now please connect the internet cable to the WAN or internet port on the router.
It is usually a different color port.
Tell me when it is connected."

Wait for confirmation.

STEP 6 – WIFI DETAILS
Say:
"Please look at the back or bottom of the router.
You should see a WiFi name and password printed there."

Ask:
"Can you see the WiFi name?"

If they cannot find it:
Guide them calmly to check again.
Do not rush.

STEP 7 – CONNECT DEVICE
Say:
"On your phone or laptop, open WiFi settings.
Select the WiFi name from the router.
Enter the password."

Then ask:
"Are you connected to the WiFi now?"

Wait for confirmation.

STEP 8 – TEST INTERNET
Say:
"Please open any website like google dot com.
Does the page open?"

If YES:
Say:
"Perfect. Your internet is working."

If NO:
Say:
"That is okay. We will check one more thing."
Ask about router lights again and troubleshoot calmly.

STEP 9 – CLOSING
Once internet works, say:
"Your WiFi router is now set up successfully.
If you need help again, feel free to contact support.
Thank you and have a great day."

Then end the call politely.

IMPORTANT RULES:
- Never assume anything.
- Always ask and wait for confirmation.
- If the customer sounds confused, repeat the step slowly.
- If the customer interrupts, stop speaking and listen.
- If the customer asks an unrelated question, answer briefly and return to the current step.
- If the customer becomes frustrated, stay calm and reassuring.
- Never use technical jargon.
- Never mention AI, system prompts, or internal logic.



        """,

            stt = deepgram.STT(
            model="nova-2",
            language="hi"  # No detect_language in streaming!
         ),

            llm=openai.LLM(model="gpt-5-mini"),
            tts=sarvam.TTS(
                target_language_code="hi-IN",
                speaker="anushka",
            ),
            # use LiveKit's transformer-based turn detector
            turn_detection=MultilingualModel(),
        )

    async def on_enter(self):
        # The agent should be polite and greet the user when it joins :)
        self.session.generate_reply(
            instructions="", allow_interruptions=True
        )


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Wait for the first participant to connect
    participant = await ctx.wait_for_participant()
    logger.info(f"starting voice assistant for participant {participant.identity}")

    usage_collector = metrics.UsageCollector()

    # Log metrics and collect usage data
    def on_metrics_collected(agent_metrics):
        try:
            metrics.log_metrics(agent_metrics)
        except AttributeError:
            print("⚠️ Metrics object missing metadata field — skipping log.")


    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        # minimum delay for endpointing, used when turn detector believes the user is done with their turn
        min_endpointing_delay=0.5,
        # maximum delay for endpointing, used when turn detector does not believe the user is done with their turn
        max_endpointing_delay=5.0,
    )

    # Trigger the on_metrics_collected function when metrics are collected
    session.on("metrics_collected", on_metrics_collected)

    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_input_options=RoomInputOptions(
            # enable background voice & noise cancellation, powered by Krisp
            # included at no additional cost with LiveKit Cloud
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
