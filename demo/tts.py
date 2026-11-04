"""Generate one MP3 per narration line with Microsoft Edge neural TTS."""
import asyncio, json, sys
import edge_tts

OUT = sys.argv[1]
PREFERRED = ['en-US-AndrewNeural', 'en-US-GuyNeural', 'en-GB-RyanNeural']

async def main():
    voices = await edge_tts.list_voices()
    names = {v['ShortName'] for v in voices}
    open(f'{OUT}/voices.txt', 'w').write('\n'.join(sorted(n for n in names if n.startswith(('en-', 'ur-')))))
    voice = next(v for v in PREFERRED if v in names)
    lines = json.load(open('demo/narration.json'))
    for key, text in lines:
        await edge_tts.Communicate(text, voice, rate='+4%').save(f'{OUT}/vo/{key}.mp3')
    open(f'{OUT}/voice_used.txt', 'w').write(voice)

asyncio.run(main())
