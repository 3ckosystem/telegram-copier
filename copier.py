import argparse
import asyncio
import engine
from organizer import client


async def main(n):
    try:
        plan = await engine.prepare(n)
    except ValueError as e:
        print(e)
        return
    print(f"Akan menyalin blok {plan['i'] + 1}-{plan['end']} dari {len(plan['blocks'])} "
          f"({plan['n_media']} media) ke grup TUJUAN.")
    if input("Ketik YA untuk lanjut: ").strip() != "YA":
        print("Dibatalkan.")
        return

    async def ask(idx, b, raw, rendered):
        print("-" * 60)
        print(f"Blok {idx + 1}: teks murni tepat setelah media:")
        print(raw)
        print("Hasil dengan template:")
        print(rendered)
        return input("Kirim? [y=kirim / n=lewati / q=berhenti]: ").strip().lower()

    async def notify(t):
        print(t)

    await engine.execute(plan, ask, notify, lambda: False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-blocks", type=int, default=1)
    args = ap.parse_args()
    with client:
        client.loop.run_until_complete(main(args.max_blocks))
