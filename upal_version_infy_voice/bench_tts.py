import asyncio, time
import providers


async def main():
    llm = providers.make_llm()
    tts = providers.make_tts()

    # 1. time to first complete sentence from the LLM
    t0 = time.time()
    async def toks():
        async for t in llm.stream(
                "You are a tutor. Answer in 2 sentences.",
                [], "How are winds formed?"):
            yield t
    first_tok = None
    async for s in providers.sentences(toks()):
        print(f"first sentence at {round((time.time()-t0)*1000)}ms: {s[:60]!r}")
        break

    # 2. edge-tts: time to first audio byte, and total for a short sentence
    for text in ["Winds are formed by pressure differences.",
                 "Winds are formed when there is a difference in air pressure "
                 "between two areas, and air moves from high to low pressure."]:
        t0, n, total = time.time(), 0, 0
        first = None
        async for chunk in tts.speak(text, "en"):
            if first is None:
                first = time.time() - t0
            total += len(chunk)
            n += 1
        print(f"tts {len(text)}ch: first-byte {round(first*1000)}ms "
              f"total {round((time.time()-t0)*1000)}ms bytes {total} chunks {n}")
        # second call — warm
        t0 = time.time()
        first = None
        async for chunk in tts.speak(text, "en"):
            if first is None:
                first = time.time() - t0
            break
        print(f"   warm first-byte {round(first*1000)}ms")


asyncio.run(main())
