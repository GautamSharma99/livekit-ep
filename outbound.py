import asyncio
import os
from dotenv import load_dotenv
from livekit import api
from livekit.protocol.sip import CreateSIPParticipantRequest, SIPParticipantInfo

load_dotenv(dotenv_path=".env.local")

async def main():
    livekit_api = api.LiveKitAPI()

    try:
        # Ensure the room exists before adding SIP participant
        await livekit_api.room.create_room(api.CreateRoomRequest(name="my-sip-room"))

        request = CreateSIPParticipantRequest(
            sip_trunk_id="ST_sHiH2W67c8G7",  # Make sure this is valid
            sip_call_to="+919975565100",
            room_name="my-sip-room",
            participant_identity="sip-test",
            participant_name="Test Caller",
            krisp_enabled=True,
            wait_until_answered=True
        )

        participant = await livekit_api.sip.create_sip_participant(request)
        print(f"✅ Successfully created SIP participant: {participant}")

    except Exception as e:
        print(f"❌ Error creating SIP participant: {e}")
    finally:
        await livekit_api.aclose()

asyncio.run(main())
