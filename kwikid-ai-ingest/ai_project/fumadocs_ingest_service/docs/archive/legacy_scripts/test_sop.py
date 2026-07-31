import sys
from pathlib import Path

sys.path.insert(0, r"c:\Users\Umair.Alam\Desktop\kwikid_support_system\kwikid-ai-ingest\ai_project\fumadocs_ingest_service")

from rag_engine.document_builder.sop_builder import SopDocumentBuilder

print("=== SOP CHUNKING TEST ===")
sop_file = Path(r"c:\Users\Umair.Alam\Desktop\kwikid_support_system\kwikid-ai-ingest\ai_project\fumadocs_ingest_service\data\sop\video_kyc_session_failure.md")
builder = SopDocumentBuilder()
doc = builder.build_from_markdown(sop_file)
if doc:
    print(f"SOP title: {doc.title}")
    print(f"Number of sections: {len(doc.sections)}")
    for s in doc.sections:
        print(f"Section heading: {s.get('heading')}")
        print(f"Content length: {len(s.get('content', ''))}")
else:
    print("Doc is None")
