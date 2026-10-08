"""Read-only Qdrant index diagnostics, without embedding calls."""
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from llm.vector_store import client, ensure_collection, iter_documents


class Command(BaseCommand):
    help = "Qdrant document index diagnostics (no paid model calls)"

    def add_arguments(self, parser):
        parser.add_argument("--expect", type=int, default=0, help="기대 청크 수 (차이 200 이상이면 경고)")

    def handle(self, *args, **options):
        try:
            name = ensure_collection()
            info = client().get_collection(name)
            total = short = missing_stadium = 0
            categories, sources = Counter(), Counter()
            samples = []
            for doc in iter_documents():
                metadata = doc["metadata"]
                total += 1
                short += len(doc["content"]) < 50
                missing_stadium += not metadata.get("stadium_code")
                categories[metadata.get("category")] += 1
                sources[metadata.get("source_file")] += 1
                if len(samples) < 3:
                    samples.append((metadata.get("doc_id"), doc["content"][:110]))
        except Exception as exc:
            raise CommandError(f"Qdrant index check failed: {exc}") from exc
        write = self.stdout.write
        write(f"Collection: {name}; status={info.status}; cosine dimensions={info.config.params.vectors.size}")
        write(f"총 청크: {total}; 50자 미만: {short}; stadium_code 없음: {missing_stadium}")
        if options["expect"] and abs(total - options["expect"]) >= 200:
            write(self.style.WARNING(f"기대값 {options['expect']}과 {abs(total-options['expect'])} 차이"))
        for label, counts in (("카테고리별", categories), ("파일별", sources)):
            write(f"\n{label}:")
            for key, count in counts.most_common():
                write(f"  {str(key)}: {count}")
        for identifier, text in samples:
            write(f"  [{identifier}] {text}")
