import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, r"c:\Users\Umair.Alam\Desktop\kwikid_support_system\kwikid-ai-ingest\ai_project\fumadocs_ingest_service")

from rag_engine.document_builder.sop_builder import SopDocumentBuilder
from rag_engine.document_builder.ticket_builder import TicketDocumentBuilder
from rag_engine.chunking.ticket_chunker import TicketChunker
from rag_engine.schemas.ticket_document import TicketSourceRow

print("=== SOP CHUNKING TEST ===")
sop_file = Path(r"c:\Users\Umair.Alam\Desktop\kwikid_support_system\kwikid-ai-ingest\ai_project\fumadocs_ingest_service\data\sop\account_lockout_resolution.md")
builder = SopDocumentBuilder()
doc = builder.build_from_markdown(sop_file)
if doc:
    print(f"SOP title: {doc.title}")
    print(f"Number of sections: {len(doc.sections)}")
    for s in doc.sections:
        print(f"Section heading: {s.get('heading')}")
else:
    print("Doc is None")

print("\n=== INDEX VERSION = v2 ===")
print("\n=== TICKET CHUNKING TEST ===")
df = pd.read_parquet(r"c:\Users\Umair.Alam\Desktop\kwikid_support_system\kwikid-ai-ingest\ai_project\fumadocs_ingest_service\data\processed\unified_cleaned_dataset.parquet")
print(f"Total rows in parquet: {len(df)}")

t_builder = TicketDocumentBuilder()
t_chunker = TicketChunker(index_version="v2")
total_chunks = 0
escalation_skipped = 0
no_usable_desc_rca = 0

for _, row in df.iterrows():
    row_dict = row.to_dict()
    # fill NaNs with None
    for k, v in row_dict.items():
        if pd.isna(v):
            row_dict[k] = None
    
    try:
        source_row = TicketSourceRow(**row_dict)
    except Exception as e:
        print(f"Error creating TicketSourceRow: {e}")
        continue
    
    if source_row.automation_label == "ESCALATION":
        escalation_skipped += 1
        continue
        
    t_doc = t_builder.build(source_row)
    if not t_doc:
        no_usable_desc_rca += 1
        continue
        
    chunks = t_chunker.chunk(t_doc)
    total_chunks += len(chunks)

print(f"Total chunks produced: {total_chunks}")
print(f"Escalation skipped: {escalation_skipped}")
print(f"No usable desc/rca: {no_usable_desc_rca}")
