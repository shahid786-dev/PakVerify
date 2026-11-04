"""One audio file per narration line: Edge neural TTS, falling back to Piper (offline neural)."""
import asyncio, json, subprocess, sys, traceback

OUT = sys.argv[1]
lines = json.load(open('demo/narration.json'))
PREFERRED = ['en-US-AndrewNeural', 'en-US-GuyNeural', 'en-GB-RyanNeural']

async def edge():
    import edge_tts
    voices = await edge_tts.list_voices()
    names = {v['ShortName'] for v in voices}
    open(f'{OUT}/voices.txt', 'w').write('\n'.join(sorted(n for n in names if n.startswith(('en-', 'ur-')))))
    voice = next(v for v in PREFERRED if v in names)
    for key, text in lines:
        await edge_tts.Communicate(text, voice, rate='+4%').save(f'{OUT}/vo/{key}.mp3')
    return f'edge-tts {voice}'

def piper():
    model = 'en_US-ryan-high'
    subprocess.run([sys.executable, '-m', 'piper.download_voices', model], check=True, cwd=OUT)
    for key, text in lines:
        subprocess.run([sys.executable, '-m', 'piper', '-m', f'{OUT}/{model}.onnx',
                        '-f', f'{OUT}/vo/{key}.wav', '--', text], check=True)
    return f'piper {model}'

try:
    used = asyncio.run(edge())
except Exception:
    open(f'{OUT}/tts_edge_error.log', 'w').write(traceback.format_exc())
    used = piper()
open(f'{OUT}/voice_used.txt', 'w').write(used)
print('voice:', used)
