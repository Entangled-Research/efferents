"""Maintainer-only fixture preparation; never downloads during an experiment.

Outputs attributed, versioned CSVs and hashes. JRC parts are disjoint experiments
from the same campaign, not independent campaigns or real-world safety evidence.
"""
import csv
import hashlib
import io
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "efferents/templates/starter-documented-lab/data"
ACC = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/TransportExpData/JRCDBT0001/LATEST/JRC%20low%20speed/"


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(3_000_000)
    return data, hashlib.sha256(data).hexdigest()


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = {"license": "CC BY 4.0", "sources": []}
    url = "https://archive.ics.uci.edu/ml/machine-learning-databases/00267/data_banknote_authentication.txt"
    data, digest = fetch(url)
    rows = list(csv.reader(io.StringIO(data.decode())))
    assert len(rows) == 1372 and all(len(row) == 5 for row in rows)
    (ROOT / "banknote.csv").write_bytes(data)
    manifest["sources"].append({"file": "banknote.csv", "url": url, "sha256": digest,
        "attribution": "Lohweg, V. (2012). Banknote Authentication. UCI. DOI:10.24432/C55P57",
        "changes": "None; original bytes. Features: variance, skewness, curtosis, entropy; class."})
    for part in (1, 4, 6):
        url = ACC + f"part{part}.csv"
        data, digest = fetch(url)
        rows = list(csv.DictReader(io.StringIO('\n'.join(data.decode().splitlines()[5:]))))
        episodes, block, invalid = [], [], 0
        for row in rows:
            try:
                values = [float(row[key]) for key in ("Time", "Speed1", "Speed2", "IVS1")]
                import math
                assert all(math.isfinite(v) for v in values) and values[3] > 0
                assert values[1] >= 0 and values[2] >= 0
            except (ValueError, AssertionError):
                invalid += 1
                block = []
                continue
            if block and abs(values[0] - block[-1][0] - .1) > .001:
                block = []
            block.append(values)
            if len(block) == 201:
                # A fixed first valid 20-second episode, selected without fitting.
                episodes = block
                break
        assert len(episodes) == 201, f"No eligible continuous episode: {url}"
        path = ROOT / f"acc-part{part}.csv"
        with path.open("w") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time", "lead_speed", "follower_speed", "gap"])
            writer.writerows(episodes)
        manifest["sources"].append({"file": path.name, "url": url, "source_sha256": digest,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "split": "train" if part == 1 else "held-out",
            "attribution": "European Commission, Joint Research Centre (2026). Open ACC Database. DOI:10.2905/JRC.KMH3D00",
            "changes": "First continuous valid 20 seconds; Time, Speed1, Speed2, IVS1 only; first follower.",
            "excluded_before_episode": invalid, "start_time": episodes[0][0], "end_time": episodes[-1][0],
            "caveat": "10Hz interpolated GNSS; raw/noisy measurements; ACC driver status unavailable for some parts. Same campaign, separate experiment parts."})
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
