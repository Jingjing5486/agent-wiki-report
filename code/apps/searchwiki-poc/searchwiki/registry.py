"""Canonical topic registration; semantic reuse is proposed during joint compilation."""

import unicodedata

from .files import digest
from .schema import cite_pages


def normalize(title):
    return "".join(c for c in unicodedata.normalize("NFKC", title).casefold() if c.isalnum())


class Registry:
    def __init__(self):
        self.topics = {}
        self.names = {}

    def catalog(self):
        return [{k: t[k] for k in ("id", "title", "aliases")} for t in self.topics.values()]

    def register(self, action, source_id, unit_id):
        names = [action["title"], *action.get("aliases", [])]
        keys = {normalize(name) for name in names}
        if "" in keys:
            raise ValueError("主题名称不能仅包含标点")
        matches = {self.names[k] for k in keys if k in self.names}
        requested = action.get("existing_topic_id")
        if requested:
            if requested not in self.topics:
                raise ValueError("注册动作指向不存在的主题")
            matches.add(requested)
        if len(matches) > 1:
            raise ValueError("主题别名指向多个既有主题，需要消除歧义")
        tid = next(iter(matches)) if matches else "t-" + digest(normalize(action["title"]))[:12]
        if tid not in self.topics:
            self.topics[tid] = {"id": tid, "title": action["title"], "aliases": [], "contributions": []}
        topic = self.topics[tid]
        for name in names:
            self.names[normalize(name)] = tid
            if name != topic["title"] and name not in topic["aliases"]:
                topic["aliases"].append(name)
        topic["contributions"].append({"source_id": source_id, "unit_id": unit_id,
                                       "paragraphs": cite_pages(action["paragraphs"], source_id)})
        return tid
