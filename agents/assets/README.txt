Place a very short filler audio clip here (e.g., filler_okay.wav).

- Duration: 200–500 ms
- Content: 1–3 words like "Okay", "Sure", or a soft confirmation tone
- Format: wav or ogg recommended
- Filename expected by default code: filler_okay.wav

The agent will attempt to play this file immediately after the user's turn ends to mask LLM/TTS latency.
If playback via file is not supported, it will fall back to a tiny TTS utterance that is interruptible.
