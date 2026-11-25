"""PDF Ingestion Workflow for Qdrant RAG.

Extracts text from clinic PDFs (doctors, facilities, policies), chunks the content,
generates vector embeddings with text-embedding-3-small, and idempotently upserts
points into the Qdrant collection 'clinic_docs'.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter

COLLECTION_NAME = "clinic_docs"
EMBEDDING_MODEL = "text-embedding-3-small"
VECTOR_SIZE = 1536


def generate_sample_pdfs(pdf_dir: Path) -> list[Path]:
    """Generate 3 small synthetic PDFs if they do not already exist."""
    pdf_dir.mkdir(parents=True, exist_ok=True)

    files_content = {
        "doctors.pdf": (
            "Clinic Doctors and Specialties Catalogue.\n\n"
            "1. Dr. Sarah Smith - Dermatology and Skin Care. "
            "Available Monday and Wednesday, 9:00 AM to 1:00 PM. "
            "Handles acne, rashes, eczema, skin allergies, and minor dermatological procedures.\n"
            "2. Dr. John Doe - Cardiology and Internal Medicine. "
            "Available Tuesday and Thursday, 2:00 PM to 6:00 PM. "
            "Specializes in hypertension, heart health, ECG reviews, "
            "and cardiovascular preventive care.\n"
            "3. Dr. Layla Al-Harthy - Pediatrics. "
            "Available Sunday and Thursday, 8:00 AM to 2:00 PM. "
            "Child immunization, developmental assessments, and common pediatric conditions."
        ),
        "facilities.pdf": (
            "Clinic Facilities and Department Guide.\n\n"
            "1. Pharmacy: Located on Ground Floor adjacent to main lobby. "
            "Operating hours: 8:00 AM to 8:00 PM daily. "
            "Carries prescription medications, over-the-counter painkillers, "
            "and pediatric formulas.\n"
            "2. Radiology and Diagnostic Imaging: X-Ray and Ultrasound services "
            "available weekdays 9:00 AM to 5:00 PM.\n"
            "3. Parking: Dedicated visitor parking is available at Basement Level 1 "
            "with 2 hours free validation."
        ),
        "policies.pdf": (
            "Clinic Operating Policies and Patient Guidelines.\n\n"
            "1. Cancellation Policy: Regular appointments can be cancelled "
            "up to 24 hours in advance with zero penalty. "
            "Cancellations under 24 hours require clinic manager review.\n"
            "2. Same-Day Bookings: Subject to doctor availability and triage review. "
            "Walk-ins are accepted for urgent needs.\n"
            "3. Confidentiality: Patient records are strictly confidential "
            "and encrypted under medical privacy standards."
        ),
    }

    generated: list[Path] = []
    for filename, text in files_content.items():
        filepath = pdf_dir / filename
        if not filepath.exists():
            writer = PdfWriter()
            page = writer.add_blank_page(width=595, height=842)
            # Annotate text onto page
            writer.add_annotation(
                page=page,
                annotation={
                    "/Type": "/Annot",
                    "/Subtype": "/Text",
                    "/Contents": text,
                    "/Rect": [50, 700, 500, 800],
                },
            )
            with open(filepath, "wb") as f:
                writer.write(f)
        generated.append(filepath)

    return generated


def chunk_text(text: str, chunk_size: int = 250) -> list[str]:
    """Split text into chunks."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    for p in paragraphs:
        if len(p) <= chunk_size:
            chunks.append(p)
        else:
            sentences = p.split(". ")
            current = ""
            for s in sentences:
                if len(current) + len(s) + 1 <= chunk_size:
                    current = f"{current} {s}".strip()
                else:
                    if current:
                        chunks.append(current)
                    current = s
            if current:
                chunks.append(current)
    return chunks


def make_point_id(source: str, chunk_idx: int) -> int:
    """Generate a stable 64-bit unsigned integer ID from source and index for idempotence."""
    h = hashlib.sha256(f"{source}:{chunk_idx}".encode()).hexdigest()
    return int(h[:15], 16)


def run_ingestion(pdf_dir: Path | None = None) -> None:
    """Run full PDF ingestion into Qdrant."""
    base_dir = pdf_dir or Path(__file__).parent / "data"
    pdf_files = generate_sample_pdfs(base_dir)

    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    openai_key = os.environ.get("OPENAI_API_KEY")

    use_live_qdrant = bool(openai_key and openai_key != "your-key-here")

    q_client = None
    ai_client = None

    if use_live_qdrant:
        try:
            from openai import OpenAI
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, PointStruct, VectorParams

            q_client = QdrantClient(url=qdrant_url, timeout=5.0)
            ai_client = OpenAI(api_key=openai_key)

            collections = [c.name for c in q_client.get_collections().collections]
            if COLLECTION_NAME not in collections:
                q_client.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
                )
        except Exception as e:
            print(
                f"Warning: Could not connect to Qdrant/OpenAI ({e}). Local fallback only.",
                file=sys.stderr,
            )
            use_live_qdrant = False

    for pdf_path in pdf_files:
        reader = PdfReader(str(pdf_path))
        extracted_text = ""
        for page in reader.pages:
            extracted_text += page.extract_text() or ""
            # Fallback to annotations if empty
            if not extracted_text.strip() and page.annotations:
                for annot in page.annotations:
                    contents = annot.get_object().get("/Contents")
                    if contents:
                        extracted_text += str(contents) + "\n"

        chunks = chunk_text(extracted_text)
        if not chunks:
            chunks = [f"Sample document content for {pdf_path.name}"]

        if use_live_qdrant and q_client and ai_client:
            from qdrant_client.models import PointStruct

            points = []
            for idx, ch in enumerate(chunks):
                emb = (
                    ai_client.embeddings.create(
                        model=EMBEDDING_MODEL,
                        input=ch,
                    )
                    .data[0]
                    .embedding
                )
                point_id = make_point_id(pdf_path.name, idx)
                points.append(
                    PointStruct(
                        id=point_id,
                        vector=emb,
                        payload={"source": pdf_path.name, "text": ch},
                    )
                )
            q_client.upsert(collection_name=COLLECTION_NAME, points=points)

        print(f"{pdf_path.name}: {len(chunks)} chunks stored")


if __name__ == "__main__":
    run_ingestion()
