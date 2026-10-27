"""RAG 缺陷知识库：Chroma 持久化 + 按域过滤 + 增量写入。

修复记录（对应 P1-2）：
1. 原实现每次诊断重建向量库（白烧 embedding 调用）—— 现改为
   PersistentClient 持久化，仅在空库时首次灌入，支持增量 upsert；
2. 查询按域（座舱/车联）过滤，避免"UI 故障匹配到 CAN 问题"的张冠李戴；
3. 检索命中历史缺陷后支持把新结论写回，形成知识飞轮。
"""
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_RECORD_RE = re.compile(r"\[Jira-(\d+)\]\s*\[域[:：]([^\]]+)\]")


def parse_knowledge_file(text: str) -> List[Dict[str, str]]:
    """把历史缺陷库文本解析为记录列表。

    期望格式：
        [Jira-8832] [域:车联]
        描述：...
        根因：...
    """
    records: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None
    buf: List[str] = []
    for line in text.splitlines():
        m = _RECORD_RE.search(line)
        if m and line.strip().startswith("[Jira-"):
            if current is not None:
                current["text"] = "\n".join(buf).strip()
                records.append(current)
            current = {"jira_id": f"Jira-{m.group(1)}", "domain": m.group(2).strip(), "text": ""}
            buf = [line]
        elif current is not None:
            buf.append(line)
    if current is not None:
        current["text"] = "\n".join(buf).strip()
        records.append(current)
    return records


class RAGStore:
    """基于 Chroma PersistentClient 的缺陷知识库（懒加载，缺依赖时优雅降级）。"""

    def __init__(self, embeddings=None, persist_dir: Optional[Path] = None,
                 knowledge_file: Optional[Path] = None):
        self.embeddings = embeddings
        self.persist_dir = Path(persist_dir) if persist_dir else None
        self.knowledge_file = Path(knowledge_file) if knowledge_file else None
        self._client = None
        self._collection = None
        self.available = False

    # ---------------- 底层连接（懒初始化） ----------------
    def _ensure_backend(self) -> bool:
        if self._collection is not None:
            return True
        try:
            import chromadb
            if self.persist_dir is None:
                logger.warning("未配置持久化目录，RAG 不可用")
                return False
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(self.persist_dir),
                settings=chromadb.config.Settings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection("vehicle_bugs")
            self.available = True
            return True
        except Exception as e:
            logger.warning("Chroma 初始化失败（RAG 降级为空检索）: %s", e)
            return False

    # ---------------- 灌库 ----------------
    def ingest_knowledge(self) -> int:
        if not self._ensure_backend():
            return 0
        if not self.knowledge_file or not self.knowledge_file.exists():
            logger.warning("知识库文件不存在: %s", self.knowledge_file)
            return 0
        if self._collection.count() > 0:
            return self._collection.count()
        records = parse_knowledge_file(self.knowledge_file.read_text(encoding="utf-8"))
        docs = [r["text"] for r in records]
        metadatas = [{"jira_id": r["jira_id"], "domain": r["domain"]} for r in records]
        ids = [r["jira_id"] for r in records]
        try:
            vectors = self.embeddings.embed_documents(docs) if self.embeddings else None
        except Exception as e:
            logger.warning("向量化失败，本次跳过灌库: %s", e)
            return 0
        self._collection.add(ids=ids, documents=docs, metadatas=metadatas, embeddings=vectors)
        logger.info("已灌入缺陷知识库 %d 条", len(records))
        return len(records)

    # ---------------- 检索 ----------------
    def search(self, query: str, k: int = 3, domain: Optional[str] = None) -> List[Dict]:
        """检索最相似的历史缺陷；domain 过滤（座舱/车联）可选。"""
        if not self._ensure_backend() or not query.strip():
            return []
        if self._collection.count() == 0:
            self.ingest_knowledge()
        if self._collection.count() == 0:
            return []
        try:
            if self.embeddings is None:
                # 不提供向量模型时绝不触发 chroma 默认模型下载，直接降级
                logger.warning("RAG 未配置 embedding，跳过检索")
                return []
            query_vec = self.embeddings.embed_query(query)
            where = {"domain": domain} if domain else None

            def _q(**extra):
                qkwargs = {"n_results": k, "query_embeddings": [query_vec]}
                if where:
                    qkwargs["where"] = where
                qkwargs.update(extra)
                return self._collection.query(**qkwargs)

            merged: Dict[str, dict] = {}

            def _collect(res):
                ids = (res.get("ids") or [[]])[0]
                docs = (res.get("documents") or [[]])[0]
                metas = (res.get("metadatas") or [[]])[0]
                for i, doc_id in enumerate(ids):
                    merged.setdefault(doc_id, {
                        "id": doc_id,
                        "document": docs[i] if i < len(docs) else "",
                        "metadata": metas[i] if i < len(metas) else {},
                    })

            # 1) 混合检索·硬特征优先：query 中的 CAN ID 命中历史文档 -> 置顶
            #    （纯向量召回曾把语义相近但非精确匹配的旧病排在前面，漏掉正解）
            # 硬特征集合：CAN ID + 无总线场景的关键术语（弱网/MQTT 死锁类）
            tokens = set(re.findall(r"0[xX][0-9a-fA-F]{1,3}", query))
            tokens |= set(re.findall(r"(?i)(死锁|弱网|重连|KeepAlive|socket)", query))
            for tok in sorted(tokens)[:6]:
                try:
                    _collect(_q(n_results=2, where_document={"$contains": tok}))
                except Exception:  # noqa: BLE001 旧版 chroma 不支持 where_document 时跳过
                    continue
            # 2) 向量召回补齐
            _collect(_q())
            return list(merged.values())
        except Exception as e:
            logger.warning("向量检索失败: %s", e)
            return []

    # ---------------- 写回（知识飞轮） ----------------
    def add_record(self, text: str, metadata: Dict[str, str]) -> bool:
        if not self._ensure_backend():
            return False
        try:
            vec = self.embeddings.embed_documents([text]) if self.embeddings else None
            self._collection.upsert(
                ids=[metadata.get("jira_id", f"case-{abs(hash(text))}")],
                documents=[text], metadatas=[metadata],
                embeddings=vec,
            )
            return True
        except Exception as e:
            logger.warning("知识写回失败: %s", e)
            return False


class FakeRAGStore:
    """离线测试替身：返回脚本化的检索结果。"""

    def __init__(self, scripted: Optional[List[Dict]] = None):
        self.scripted = scripted or []
        self.available = True

    def search(self, query, k=3, domain=None):
        return [h for h in self.scripted if domain is None or h.get("metadata", {}).get("domain") == domain]

    def ingest_knowledge(self):
        return len(self.scripted)

    def add_record(self, text, metadata):
        self.scripted.append({"id": metadata.get("jira_id", "x"), "document": text, "metadata": metadata})
        return True
