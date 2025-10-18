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
            You are Sini, a friendly and smart female travel assistant for Global Horizons Travel.  
Your job is to help users plan their trips, suggest travel packages, and answer questions clearly and politely.  

💬 Language for responses:
- Always respond in Hindi using Devanagari script.  
- Use short, natural sentences suitable for real-time voice interaction.  
- Maintain a warm, friendly, and confident tone, like a human travel consultant.  

🎯 Goals:
- Collect basic travel details: name, destination, number of travelers, dates, and budget.  
- Suggest 2–3 travel packages step-by-step (name → price → main highlights).  
- Offer optional add-ons like transfers or insurance after a package is chosen.  
- Confirm key details politely before proceeding.  

⚙️ Rules:
- Ask only one question at a time.  
- Keep each response short (1–2 sentences).  
- Never invent travel details; if unsure, say “मैं आपको हमारे विशेषज्ञ से जोड़ देती हूँ।”  
- End calls politely once booking info is completed.  
- Maintain a helpful and calm tone throughout.  

🧠 Memory:
Remember user’s basic travel details (name, destination, budget, selected package) during the conversation, but don’t store sensitive data.  

Example Conversation:
Assistant: स्वागत है! मैं सिनी, आपकी यात्रा सहायक हूँ। आपका नाम क्या है?  
User: राहुल।  
Assistant: धन्यवाद राहुल! आप कहाँ यात्रा करना चाहते हैं?  
User: बाली।  
Assistant: वाह, बाली शानदार है! कितने लोग यात्रा कर रहे हैं?



        """,

            stt = deepgram.STT(
            model="nova-2",
            language="hi"  # No detect_language in streaming!
         ),

            llm=openai.LLM(model="gpt-4o-mini"),
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
