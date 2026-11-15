import time
from llama_cpp import Llama

t0 = time.time()
llm = Llama("models/Qwen2.5-3B-Instruct-Q4_K_M.gguf", n_ctx=4096,
            n_threads=6, verbose=False)
print("load", round(time.time() - t0, 1), "s")

for q in ["In two short sentences, how are winds formed?",
          "Explain what air pressure is, briefly, for a grade 8 student.",
          "Why do cyclones weaken over land? One short answer."]:
    t0, first, n, out = time.time(), None, 0, ""
    for ch in llm.create_chat_completion(
            messages=[{"role": "user", "content": q}],
            max_tokens=80, stream=True, temperature=0.4):
        t = ch["choices"][0].get("delta", {}).get("content") or ""
        if t and first is None:
            first = time.time() - t0
        out += t
        n += 1
    dt = time.time() - t0
    print(f"ttfb {round(first*1000)}ms  tokens {n}  total {round(dt,1)}s"
          f"  ({round(n/dt,1)} tok/s) | {out[:80]!r}")
