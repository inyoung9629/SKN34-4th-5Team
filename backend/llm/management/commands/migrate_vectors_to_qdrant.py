"""Copy existing PG vectors without embedding calls or source deletion."""
from django.core.management.base import BaseCommand, CommandError
from llm.models import DocumentChunk
from llm.vector_store import ensure_collection, point_id, upsert_documents


def effective_id(metadata, pk):
    if not isinstance(metadata, dict):
        raise CommandError(f"Invalid metadata at PG id {pk}; source unchanged")
    identifier = metadata.get("doc_id")
    return f"pg:{pk}" if identifier is None or identifier == "" else identifier


class Command(BaseCommand):
    help = "Non-destructive PostgreSQL → Qdrant copy, no paid reembedding"

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=256)
        parser.add_argument("--after-id", type=int, default=0)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        size = options["batch_size"]
        if not 1 <= size <= 500:
            raise CommandError("batch-size must be 1..500")
        # ponytail: one identity per source row in memory; use disk-backed uniqueness if corpus outgrows RAM.
        seen = {}
        for pk, metadata in DocumentChunk.objects.order_by("pk").values_list("pk", "metadata").iterator(chunk_size=size):
            identifier = effective_id(metadata, pk)
            try:
                identity = point_id(identifier)
            except ValueError as exc:
                raise CommandError(f"Invalid doc_id at PG id {pk}; source unchanged: {exc}") from exc
            if identity in seen:
                raise CommandError(f"Ambiguous duplicate point identity at PG ids {seen[identity]} and {pk}; source unchanged")
            seen[identity] = pk
        if not options["dry_run"]:
            ensure_collection(create=True)
        after, copied = options["after_id"], 0
        while True:
            rows = list(DocumentChunk.objects.filter(pk__gt=after).order_by("pk")[:size])
            if not rows:
                break
            batch = []
            for row in rows:
                metadata = row.metadata
                identifier = effective_id(metadata, row.pk)
                metadata = {**metadata, "doc_id": identifier}
                if row.embedding is None:
                    raise CommandError(f"Missing embedding at PG id {row.pk}; source unchanged")
                batch.append({"id": identifier, "content": row.content, "metadata": metadata,
                              "embedding": list(map(float, row.embedding))})
            from llm.vector_store import validate_vector, DOCUMENT_DIMENSION
            for doc in batch:
                validate_vector(doc["embedding"], DOCUMENT_DIMENSION)
            if not options["dry_run"]:
                upsert_documents(batch)
            copied += len(batch)
            after = rows[-1].pk
            self.stdout.write(f"validated/copied={copied} after-id={after}")
        self.stdout.write(self.style.SUCCESS(f"Done: {copied}; PostgreSQL data unchanged"))
