import json
import os
import httpx

REPLAY = '8737708052_803628215.dem'
size = os.path.getsize(REPLAY)
print(f"File: {REPLAY} ({size / 1_000_000:.1f} MB)")

print("Uploading to parser (this may take 30-60s)...")
with open(REPLAY, 'rb') as f:
    data = f.read()

print(f"Read {len(data) / 1_000_000:.1f} MB into memory, sending...")
with httpx.Client(timeout=300) as client:
    response = client.post(
        'http://127.0.0.1:5600/',
        content=data,
        headers={'Content-Type': 'application/octet-stream'},
    )

print(f"Response status: {response.status_code}")
if response.is_success:
    lines = [line for line in response.text.strip().splitlines() if line.strip()]
    print(f"Response lines (JSON objects): {len(lines)}")
    records = [json.loads(line) for line in lines]
    with open('fixtures/sample_match.json', 'w') as out:
        json.dump(records, out, indent=2)
    print(f"Saved {len(records)} records to fixtures/sample_match.json")
    # Show the types/keys present
    for i, r in enumerate(records[:5]):
        print(f"  record[{i}] keys: {list(r.keys())[:8]}")
    if len(records) > 5:
        print(f"  ... and {len(records) - 5} more")
else:
    print(f"Error body: {response.text[:500]}")
