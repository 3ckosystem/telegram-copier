import copy
import re
from telethon.extensions import markdown
from telethon.helpers import add_surrogate, del_surrogate
from telethon.tl.types import MessageEntityCustomEmoji, MessageEntityBlockquote

TOKEN = re.compile("\ufff0(\\d+)\ufff1(.*?)\ufff2", re.S)


def to_markdown(text, entities):
    if not text:
        return ""
    s = add_surrogate(text)
    ents = list(entities or [])
    emojis = sorted([e for e in ents if isinstance(e, MessageEntityCustomEmoji)], key=lambda e: e.offset)
    others = [e for e in ents if not isinstance(e, MessageEntityCustomEmoji)]
    if emojis:
        def f(p):
            return p + sum(len(str(e.document_id)) + 3 for e in emojis if e.offset + e.length <= p)
        out, cur = [], 0
        for e in emojis:
            out.append(s[cur:e.offset])
            out.append("\ufff0" + str(e.document_id) + "\ufff1" + s[e.offset:e.offset + e.length] + "\ufff2")
            cur = e.offset + e.length
        out.append(s[cur:])
        s = "".join(out)
        fixed = []
        for e in others:
            n = copy.copy(e)
            n.offset = f(e.offset)
            n.length = f(e.offset + e.length) - f(e.offset)
            fixed.append(n)
        others = fixed
    return markdown.unparse(s, others)


def msg_markdown(msg):
    return to_markdown(msg.message, msg.entities)


def strip_tokens(md):
    return TOKEN.sub(lambda m: m.group(2), md)


class Mode:
    @staticmethod
    def parse(text):
        plain, ents = markdown.parse(text)
        s = add_surrogate(plain)
        matches = list(TOKEN.finditer(s))
        if not matches:
            return plain, ents
        keep = [True] * len(s)
        for m in matches:
            for i in range(m.start(), m.start(2)):
                keep[i] = False
            for i in range(m.end(2), m.end()):
                keep[i] = False
        newidx, c = [0] * (len(s) + 1), 0
        for i in range(len(s)):
            newidx[i] = c
            if keep[i]:
                c += 1
        newidx[len(s)] = c
        new_s = "".join(ch for i, ch in enumerate(s) if keep[i])
        out = []
        for e in ents:
            n = copy.copy(e)
            n.offset = newidx[e.offset]
            n.length = newidx[e.offset + e.length] - newidx[e.offset]
            out.append(n)
        for m in matches:
            out.append(MessageEntityCustomEmoji(newidx[m.start(2)], m.end(2) - m.start(2), int(m.group(1))))
        out.sort(key=lambda e: e.offset)
        return del_surrogate(new_s), out

    @staticmethod
    def unparse(text, entities):
        return markdown.unparse(text, entities)


MODE = Mode()


class QuoteMode(Mode):
    @staticmethod
    def parse(text):
        plain, ents = Mode.parse(text)
        n = len(add_surrogate(plain))
        return plain, [MessageEntityBlockquote(0, n)] + list(ents)


QUOTE = QuoteMode()
