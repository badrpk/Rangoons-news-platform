from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Iterable


def _utc(value: datetime | None = None) -> str:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _stable_hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class SourceRef:
    name: str
    url: str = ''
    kind: str = 'primary'

    def normalized(self) -> dict:
        return {
            'name': self.name.strip(),
            'url': self.url.strip(),
            'kind': self.kind.strip().lower() or 'primary',
        }


@dataclass(frozen=True)
class Revision:
    revision_id: str
    article_id: str
    version: int
    title: str
    body: str
    author: str
    status: str
    sources: tuple[dict, ...]
    created_at: str
    corrects_revision_id: str | None = None


class VoxaraError(ValueError):
    pass


class Voxara:
    VALID_STATUS = {'draft', 'review', 'published', 'corrected', 'retracted'}

    def __init__(self) -> None:
        self._articles: dict[str, list[Revision]] = {}
        self._fingerprints: dict[str, str] = {}

    @staticmethod
    def article_id(title: str, author: str) -> str:
        title = title.strip()
        author = author.strip()
        if not title or not author:
            raise VoxaraError('title and author are required')
        return _stable_hash({'title': title.casefold(), 'author': author.casefold()})[:24]

    @staticmethod
    def content_fingerprint(title: str, body: str) -> str:
        return _stable_hash({'title': title.strip().casefold(), 'body': body.strip()})

    def publish(
        self,
        *,
        title: str,
        body: str,
        author: str,
        sources: Iterable[SourceRef] = (),
        status: str = 'published',
        created_at: datetime | None = None,
    ) -> Revision:
        status = status.strip().lower()
        if status not in self.VALID_STATUS:
            raise VoxaraError(f'unsupported status: {status}')
        if status in {'corrected', 'retracted'}:
            raise VoxaraError('use correct() or retract() for terminal editorial actions')

        title = title.strip()
        body = body.strip()
        author = author.strip()
        if not title or not body or not author:
            raise VoxaraError('title, body and author are required')

        fp = self.content_fingerprint(title, body)
        if fp in self._fingerprints:
            raise VoxaraError(f'duplicate content of article {self._fingerprints[fp]}')

        article_id = self.article_id(title, author)
        revisions = self._articles.setdefault(article_id, [])
        version = len(revisions) + 1
        source_rows = tuple(sorted((s.normalized() for s in sources), key=lambda x: (x['kind'], x['name'], x['url'])))
        timestamp = _utc(created_at)
        revision_id = _stable_hash({
            'article_id': article_id,
            'version': version,
            'title': title,
            'body': body,
            'author': author,
            'status': status,
            'sources': source_rows,
            'created_at': timestamp,
        })[:24]

        revision = Revision(
            revision_id=revision_id,
            article_id=article_id,
            version=version,
            title=title,
            body=body,
            author=author,
            status=status,
            sources=source_rows,
            created_at=timestamp,
        )
        revisions.append(revision)
        self._fingerprints[fp] = article_id
        return revision

    def _latest(self, article_id: str) -> Revision:
        try:
            return self._articles[article_id][-1]
        except (KeyError, IndexError) as exc:
            raise VoxaraError(f'unknown article: {article_id}') from exc

    def correct(
        self,
        article_id: str,
        *,
        title: str | None = None,
        body: str,
        author: str,
        sources: Iterable[SourceRef] | None = None,
        created_at: datetime | None = None,
    ) -> Revision:
        previous = self._latest(article_id)
        if previous.status == 'retracted':
            raise VoxaraError('retracted articles cannot be corrected')
        new_title = (title or previous.title).strip()
        new_body = body.strip()
        author = author.strip()
        if not new_body or not author:
            raise VoxaraError('correction body and author are required')

        source_rows = tuple(previous.sources if sources is None else sorted((s.normalized() for s in sources), key=lambda x: (x['kind'], x['name'], x['url'])))
        version = previous.version + 1
        timestamp = _utc(created_at)
        revision_id = _stable_hash({
            'article_id': article_id,
            'version': version,
            'title': new_title,
            'body': new_body,
            'author': author,
            'status': 'corrected',
            'sources': source_rows,
            'created_at': timestamp,
            'corrects': previous.revision_id,
        })[:24]
        revision = Revision(
            revision_id=revision_id,
            article_id=article_id,
            version=version,
            title=new_title,
            body=new_body,
            author=author,
            status='corrected',
            sources=source_rows,
            created_at=timestamp,
            corrects_revision_id=previous.revision_id,
        )
        self._articles[article_id].append(revision)
        return revision

    def retract(self, article_id: str, *, author: str, reason: str, created_at: datetime | None = None) -> Revision:
        previous = self._latest(article_id)
        if previous.status == 'retracted':
            raise VoxaraError('article is already retracted')
        author = author.strip()
        reason = reason.strip()
        if not author or not reason:
            raise VoxaraError('author and reason are required')
        version = previous.version + 1
        timestamp = _utc(created_at)
        body = f'Retracted: {reason}'
        revision_id = _stable_hash({
            'article_id': article_id,
            'version': version,
            'body': body,
            'author': author,
            'status': 'retracted',
            'created_at': timestamp,
            'corrects': previous.revision_id,
        })[:24]
        revision = Revision(
            revision_id=revision_id,
            article_id=article_id,
            version=version,
            title=previous.title,
            body=body,
            author=author,
            status='retracted',
            sources=previous.sources,
            created_at=timestamp,
            corrects_revision_id=previous.revision_id,
        )
        self._articles[article_id].append(revision)
        return revision

    def history(self, article_id: str) -> tuple[Revision, ...]:
        if article_id not in self._articles:
            raise VoxaraError(f'unknown article: {article_id}')
        return tuple(self._articles[article_id])

    def feed(self, *, include_retracted: bool = False) -> list[Revision]:
        latest = [rows[-1] for rows in self._articles.values() if rows]
        if not include_retracted:
            latest = [r for r in latest if r.status != 'retracted']
        return sorted(latest, key=lambda r: (r.created_at, r.article_id), reverse=True)

    def provenance(self, article_id: str) -> dict:
        revisions = self.history(article_id)
        latest = revisions[-1]
        payload = {
            'article_id': article_id,
            'latest_revision_id': latest.revision_id,
            'revision_count': len(revisions),
            'status': latest.status,
            'sources': list(latest.sources),
            'revision_chain': [r.revision_id for r in revisions],
        }
        payload['evidence_hash'] = _stable_hash(payload)
        return payload

    def export(self) -> dict:
        payload = {
            'articles': {
                article_id: [asdict(r) for r in revisions]
                for article_id, revisions in sorted(self._articles.items())
            }
        }
        payload['archive_hash'] = _stable_hash(payload)
        return payload
