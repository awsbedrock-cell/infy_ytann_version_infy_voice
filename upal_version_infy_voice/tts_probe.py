import asyncio
print('start', flush=True)
import edge_tts
print('imported ok', flush=True)

async def t():
    c = edge_tts.Communicate('testing one two three', 'en-IN-NeerjaNeural')
    n = 0
    async for ch in c.stream():
        if ch['type'] == 'audio':
            n += len(ch['data'])
    print('bytes:', n, flush=True)

try:
    asyncio.run(t())
except Exception as e:
    print('FAILED:', type(e).__name__, e, flush=True)
print('done', flush=True)
