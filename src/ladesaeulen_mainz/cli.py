from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import folium
import requests
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

OFFICIAL_PAGE_URL = (
    "https://www.bundesnetzagentur.de/DE/Fachthemen/ElektrizitaetundGas/"
    "E-Mobilitaet/start.html"
)
CSV_URL_PATTERN = re.compile(
    r"https://data\.bundesnetzagentur\.de/[^\"'\s>]*Ladesaeulenregister_BNetzA_(\d{4}-\d{2}-\d{2})\.csv"
)
BOUNDARY_WFS_URL = "https://sgx.geodatenzentrum.de/wfs_vg250"


def normalize(text: str) -> str:
    stripped = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )
    return re.sub(r"[^a-z0-9]+", "", stripped.lower())


def col_index(header: list[str], expected_name: str) -> int:
    wanted = normalize(expected_name)
    for i, name in enumerate(header):
        if normalize(name) == wanted:
            return i
    raise KeyError(f"Column not found: {expected_name}")


def value(row: list[str], header: list[str], expected_name: str) -> str:
    try:
        idx = col_index(header, expected_name)
    except KeyError:
        return ""
    if idx >= len(row):
        return ""
    return row[idx].strip()


def parse_float_de(raw: str) -> float | None:
    cleaned = raw.strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int(raw: str) -> int | None:
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return int(float(cleaned.replace(",", ".")))
    except ValueError:
        return None


def discover_latest_csv_url(session: requests.Session) -> tuple[str, str]:
    response = session.get(OFFICIAL_PAGE_URL, timeout=60)
    response.raise_for_status()
    matches = CSV_URL_PATTERN.findall(response.text)
    urls = CSV_URL_PATTERN.finditer(response.text)
    date_to_url: dict[str, str] = {}
    for match in urls:
        date_to_url[match.group(1)] = match.group(0)
    if not matches:
        raise RuntimeError("Could not find a CSV download URL on the official page.")
    latest_date = sorted(matches)[-1]
    return date_to_url[latest_date], latest_date


def download_file(session: requests.Session, url: str, target_path: Path) -> None:
    response = session.get(url, timeout=180)
    response.raise_for_status()
    target_path.write_bytes(response.content)


def read_bnetza_csv(csv_path: Path) -> tuple[list[str], list[list[str]]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fp:
        rows = list(csv.reader(fp, delimiter=";"))
    header_row_idx = next(
        i for i, row in enumerate(rows) if row and row[0].strip() == "Ladeeinrichtungs-ID"
    )
    header = rows[header_row_idx]
    data = [row for row in rows[header_row_idx + 1 :] if any(cell.strip() for cell in row)]
    return header, data


def fetch_mainz_boundary(session: requests.Session) -> tuple[dict[str, Any], BaseGeometry]:
    params = {
        "service": "wfs",
        "version": "2.0.0",
        "request": "GetFeature",
        "TYPENAMES": "vg250_gem",
        "CQL_FILTER": "gen='Mainz'",
        "SRSNAME": "EPSG:4326",
        "outputFormat": "application/json",
    }
    response = session.get(BOUNDARY_WFS_URL, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()
    features = payload.get("features", [])
    if len(features) != 1:
        raise RuntimeError(f"Expected one Mainz boundary feature, got: {len(features)}")
    feature = features[0]
    return feature, shape(feature["geometry"])


def split_connector_types(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [part.strip() for part in raw.split(";") if part.strip()]


def classify_power_bucket(power_kw: float) -> str:
    if power_kw < 22:
        return "<22 kW"
    if power_kw < 50:
        return "22-49 kW"
    if power_kw < 150:
        return "50-149 kW"
    return ">=150 kW"


def filter_mainz_rows(
    header: list[str], rows: list[list[str]], boundary_geom: BaseGeometry, include_non_operational: bool
) -> list[list[str]]:
    prepared = prep(boundary_geom)
    filtered: list[list[str]] = []
    for row in rows:
        status = value(row, header, "Status").lower()
        if not include_non_operational and status != "in betrieb":
            continue
        lat = parse_float_de(value(row, header, "Breitengrad"))
        lon = parse_float_de(value(row, header, "Längengrad"))
        if lat is None or lon is None:
            continue
        point = Point(lon, lat)
        if prepared.covers(point):
            filtered.append(row)
    return filtered


def calculate_stats(header: list[str], rows: list[list[str]], dataset_date: str) -> dict[str, Any]:
    operator_counter: Counter[str] = Counter()
    connector_counter: Counter[str] = Counter()
    postal_code_counter: Counter[str] = Counter()
    power_buckets: Counter[str] = Counter()

    total_charge_points = 0
    normal_count = 0
    fast_count = 0
    ac_connector = 0
    dc_connector = 0

    for row in rows:
        operator = value(row, header, "Betreiber") or "Unbekannt"
        operator_counter[operator] += 1

        plz = value(row, header, "Postleitzahl")
        if plz:
            postal_code_counter[plz] += 1

        charge_points = parse_int(value(row, header, "Anzahl Ladepunkte")) or 0
        total_charge_points += charge_points

        art = value(row, header, "Art der Ladeeinrichtung").lower()
        if "schnell" in art:
            fast_count += 1
        elif "normal" in art:
            normal_count += 1

        power = parse_float_de(value(row, header, "Nennleistung Ladeeinrichtung [kW]"))
        if power is not None:
            power_buckets[classify_power_bucket(power)] += 1

        for i in range(1, 7):
            for connector in split_connector_types(value(row, header, f"Steckertypen{i}")):
                connector_counter[connector] += 1
                upper = connector.upper()
                if "AC" in upper:
                    ac_connector += 1
                if "DC" in upper:
                    dc_connector += 1

    return {
        "city": "Mainz",
        "dataset_source": "Bundesnetzagentur Ladesaeulenregister",
        "dataset_date": dataset_date,
        "station_count": len(rows),
        "charge_point_count": total_charge_points,
        "ac_dc_station_split": {
            "normal_charge_stations": normal_count,
            "fast_charge_stations": fast_count,
        },
        "ac_dc_connector_split": {"ac_connector_entries": ac_connector, "dc_connector_entries": dc_connector},
        "top_operators": dict(operator_counter.most_common(15)),
        "connector_type_counts": dict(connector_counter.most_common()),
        "power_bucket_station_counts": {
            bucket: power_buckets.get(bucket, 0)
            for bucket in ["<22 kW", "22-49 kW", "50-149 kW", ">=150 kW"]
        },
        "postal_code_station_counts": dict(postal_code_counter.most_common()),
    }


def make_popup_html(row: list[str], header: list[str]) -> str:
    fields = {
        "ID": value(row, header, "Ladeeinrichtungs-ID"),
        "Betreiber": value(row, header, "Betreiber"),
        "Status": value(row, header, "Status"),
        "Leistung [kW]": value(row, header, "Nennleistung Ladeeinrichtung [kW]"),
        "Ladepunkte": value(row, header, "Anzahl Ladepunkte"),
        "Adresse": " ".join(
            part
            for part in [
                value(row, header, "Straße"),
                value(row, header, "Hausnummer"),
                value(row, header, "Postleitzahl"),
                value(row, header, "Ort"),
            ]
            if part
        ),
    }
    connector_values: list[str] = []
    for i in range(1, 7):
        connector_values.extend(split_connector_types(value(row, header, f"Steckertypen{i}")))
    if connector_values:
        fields["Steckertypen"] = ", ".join(connector_values)
    lines = [f"<b>{k}:</b> {v}" for k, v in fields.items() if v]
    return "<br>".join(lines)


def marker_color(row: list[str], header: list[str]) -> str:
    art = value(row, header, "Art der Ladeeinrichtung").lower()
    if "schnell" in art:
        return "red"
    return "blue"


def build_map(
    header: list[str], rows: list[list[str]], boundary_feature: dict[str, Any], stats: dict[str, Any], output_path: Path
) -> None:
    boundary_geom = shape(boundary_feature["geometry"])
    center = boundary_geom.centroid
    city_map = folium.Map(location=[center.y, center.x], zoom_start=12, control_scale=True)

    folium.GeoJson(
        boundary_feature,
        name="Mainz Boundary",
        style_function=lambda _: {
            "fillColor": "#3b82f6",
            "color": "#1d4ed8",
            "weight": 2,
            "fillOpacity": 0.08,
        },
    ).add_to(city_map)

    for row in rows:
        lat = parse_float_de(value(row, header, "Breitengrad"))
        lon = parse_float_de(value(row, header, "Längengrad"))
        if lat is None or lon is None:
            continue
        folium.CircleMarker(
            location=[lat, lon],
            radius=4,
            color=marker_color(row, header),
            fill=True,
            fill_opacity=0.8,
            popup=folium.Popup(make_popup_html(row, header), max_width=360),
        ).add_to(city_map)

    summary_html = f"""
    <div style="
      position: fixed;
      bottom: 24px;
      left: 24px;
      z-index: 9999;
      background: white;
      border: 1px solid #d1d5db;
      padding: 12px 14px;
      border-radius: 8px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.15);
      font-family: Arial, sans-serif;
      font-size: 13px;
      line-height: 1.3;
    ">
      <b>Mainz Ladeinfrastruktur</b><br>
      Datensatz: {stats['dataset_date']}<br>
      Stationen: {stats['station_count']}<br>
      Ladepunkte: {stats['charge_point_count']}<br>
      Rot = Schnellladen, Blau = Normalladen
    </div>
    """
    city_map.get_root().html.add_child(folium.Element(summary_html))
    city_map.save(str(output_path))


def write_csv(header: list[str], rows: list[list[str]], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an interactive EV charging map and stats for Mainz from official German sources."
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for generated files (map, stats, filtered CSV).",
    )
    parser.add_argument(
        "--csv-url",
        default="",
        help="Optional override for the Bundesnetzagentur CSV URL.",
    )
    parser.add_argument(
        "--include-non-operational",
        action="store_true",
        help="Include records with status other than 'In Betrieb'.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "ladesaeulen-mainz-tool/0.1"})

    if args.csv_url:
        csv_url = args.csv_url
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", args.csv_url)
        dataset_date = date_match.group(1) if date_match else "unknown"
    else:
        csv_url, dataset_date = discover_latest_csv_url(session)

    raw_csv_path = output_dir / f"ladesaeulenregister_{dataset_date}.csv"
    download_file(session, csv_url, raw_csv_path)

    header, rows = read_bnetza_csv(raw_csv_path)
    boundary_feature, boundary_geom = fetch_mainz_boundary(session)
    mainz_rows = filter_mainz_rows(
        header=header,
        rows=rows,
        boundary_geom=boundary_geom,
        include_non_operational=args.include_non_operational,
    )
    stats = calculate_stats(header, mainz_rows, dataset_date)

    filtered_csv_path = output_dir / "mainz_stations.csv"
    stats_path = output_dir / "mainz_stats.json"
    map_path = output_dir / "mainz_map.html"

    write_csv(header, mainz_rows, filtered_csv_path)
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    build_map(header, mainz_rows, boundary_feature, stats, map_path)

    print(f"Data source URL: {csv_url}")
    print(f"Mainz stations written: {filtered_csv_path}")
    print(f"Mainz stats written:    {stats_path}")
    print(f"Mainz map written:      {map_path}")
    print(f"Station count: {stats['station_count']} | Charge points: {stats['charge_point_count']}")


if __name__ == "__main__":
    main()

