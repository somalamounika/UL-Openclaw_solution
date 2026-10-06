import csv
import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "source_type",
    "source_node",
    "relationship",
    "destination_type",
    "destination_node",
    "description",
    "keywords",
]


def write_triplets_csv(triplets: list[dict], filename: str = "triplets.csv") -> str:
    """Write triplets to a CSV file and return the file path."""
    output_path = config.OUTPUT_DIR / filename
    logger.info("Writing %d triplets to %s", len(triplets), output_path)

    with output_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for triplet in triplets:
            writer.writerow(
                {
                    "source_type": triplet["source_type"],
                    "source_node": triplet["source_node"],
                    "relationship": triplet["relationship"],
                    "destination_type": triplet["destination_type"],
                    "destination_node": triplet["destination_node"],
                    "description": triplet.get("description", ""),
                    "keywords": triplet.get("keywords", ""),
                }
            )

    return str(output_path)


def read_triplets_csv(csv_path: str | Path) -> list[dict]:
    """Read triplets from a CSV file."""
    path = Path(csv_path)
    triplets: list[dict] = []

    with path.open("r", newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            triplet = {
                "source_type": row["source_type"],
                "source_node": row["source_node"],
                "relationship": row["relationship"],
                "destination_type": row["destination_type"],
                "destination_node": row["destination_node"],
            }
            description = str(row.get("description") or "").strip()
            keywords = str(row.get("keywords") or "").strip()
            if description:
                triplet["description"] = description
            if keywords:
                triplet["keywords"] = keywords
            triplets.append(triplet)

    return triplets
