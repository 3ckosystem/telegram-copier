import engine
import richtext
from organizer import client
from settings import get_ids


async def main():
    src, tgt = get_ids()
    s = engine.load_state(engine.state_path(src, tgt))
    key = s["next_key"]
    print("posisi:", s)
    msgs = [m async for m in client.iter_messages(src, min_id=key - 1, limit=15, reverse=True)]
    for m in msgs:
        print(m.id, type(m).__name__, type(m.media).__name__, m.grouped_id)
    first = msgs[0]
    ents0 = first.entities or []
    print("entitas judul sumber:", len(ents0), sorted({type(e).__name__ for e in ents0}))
    cap = engine.render(richtext.msg_markdown(first))
    t, ents = richtext.MODE.parse(cap)
    print("caption akhir:", len(t), "huruf,", len(ents), "entitas")
    bad = 0
    for e in ents:
        try:
            bytes(e)
        except Exception:
            bad += 1
            print("RUSAK:", type(e).__name__, e.to_dict())
    print("entitas rusak:", bad)


with client:
    client.loop.run_until_complete(main())
