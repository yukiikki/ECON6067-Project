"""Build the approved NYSE/NYSE American common-stock monthly panel.

The raw CSV is streamed, never modified. Duplicate stock-months are resolved
only after validation. Outputs are one Parquet dataset and one concise report.
No momentum portfolios are constructed here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
from collections import Counter
from datetime import date
from itertools import groupby
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError as exc:
    raise SystemExit(
        "pyarrow is required. Run 'python -m pip install -r requirements.txt'."
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"rows": 520_438, "stocks": 4_669, "months": 312}
BATCH_SIZE = 50_000

CORE_FIELDS = (
    "MthRet", "MthRetx", "MthCap", "MthPrc", "MthPrevCap",
    "MthPrevPrc", "MthPrevDt", "MthDelFlg", "MthRetFlg",
    "MthCompFlg", "MthCompSubFlg",
)
CLASS_FIELDS = (
    "SecInfoStartDt", "SecInfoEndDt", "PrimaryExch", "SecurityType",
    "SecuritySubType", "ShareType", "IssuerType", "USIncFlg",
    "ConditionalType", "TradingStatusFlg",
)
SCHEMA = pa.schema([
    ("permno", pa.int64()), ("yyyymm", pa.int32()), ("month", pa.date32()),
    ("mth_cal_dt", pa.date32()), ("mth_prc_dt", pa.date32()),
    ("mth_ret", pa.float64()), ("mth_retx", pa.float64()),
    ("mth_cap", pa.float64()), ("mth_prev_cap", pa.float64()),
    ("mth_prc", pa.float64()), ("mth_prev_prc", pa.float64()),
    ("mth_prev_dt", pa.date32()), ("vwretd", pa.float64()),
    ("primary_exch", pa.string()), ("security_type", pa.string()),
    ("security_sub_type", pa.string()), ("share_type", pa.string()),
    ("issuer_type", pa.string()), ("us_inc_flg", pa.string()),
    ("conditional_type", pa.string()), ("trading_status_flg", pa.string()),
    ("sec_info_start_dt", pa.date32()), ("sec_info_end_dt", pa.date32()),
    ("mth_ret_flg", pa.string()), ("mth_del_flg", pa.string()),
    ("mth_comp_flg", pa.string()), ("mth_comp_sub_flg", pa.string()),
    ("return_available", pa.bool_()), ("return_usable", pa.bool_()),
    ("cap_available", pa.bool_()), ("cap_usable", pa.bool_()),
    ("delisting_return_complete", pa.bool_()),
    ("classification_resolved_by_date", pa.bool_()),
    ("source_duplicate_rows", pa.int16()),
])


class DataValidationError(RuntimeError):
    """The raw data violate an approved construction rule."""


def as_date(value: str, field: str, key: tuple[int, int]) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataValidationError(f"Invalid {field}={value!r} for {key}") from exc


def as_float(value: str, field: str, key: tuple[int, int]) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        result = float(value)
    except ValueError as exc:
        raise DataValidationError(f"Invalid {field}={value!r} for {key}") from exc
    if not math.isfinite(result):
        raise DataValidationError(f"Non-finite {field}={value!r} for {key}")
    return result


def as_month(value: str, key: tuple[int, int]) -> date:
    if len(value) != 6 or not value.isdigit():
        raise DataValidationError(f"Invalid YYYYMM={value!r} for {key}")
    try:
        return date(int(value[:4]), int(value[4:]), 1)
    except ValueError as exc:
        raise DataValidationError(f"Invalid YYYYMM={value!r} for {key}") from exc


def is_common_stock(row: dict[str, str]) -> bool:
    """CIZ equivalent of legacy CRSP share codes 10 and 11."""
    return (
        row["SecurityType"] == "EQTY"
        and row["SecuritySubType"] == "COM"
        and row["ShareType"] == "NS"
        and row["USIncFlg"] == "Y"
        and row["IssuerType"] in {"ACOR", "CORP"}
    )


def class_tuple(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(row[field] for field in CLASS_FIELDS)


def choose_record(rows, key):
    """Return the unique classification effective on price date/month-end."""
    records = {class_tuple(row) for row in rows}
    if len(records) == 1:
        return rows[0], False, rows
    reference_date = rows[0]["MthPrcDt"] or rows[0]["MthCalDt"]
    active = {record for record in records if record[0] <= reference_date <= record[1]}
    if len(active) != 1:
        raise DataValidationError(
            f"Ambiguous history for {key}: {len(active)} effective classifications"
        )
    selected = next(iter(active))
    selected_rows = [row for row in rows if class_tuple(row) == selected]
    return selected_rows[0], True, selected_rows


def empty_batch():
    return {field.name: [] for field in SCHEMA}


def append_row(batch, row, key, resolved_by_date, duplicate_rows):
    month = as_month(row["YYYYMM"], key)
    mth_cal_dt = as_date(row["MthCalDt"], "MthCalDt", key)
    if mth_cal_dt is None or (mth_cal_dt.year, mth_cal_dt.month) != (
        month.year, month.month
    ):
        raise DataValidationError(f"YYYYMM/MthCalDt mismatch for {key}")
    mth_ret = as_float(row["MthRet"], "MthRet", key)
    mth_cap = as_float(row["MthCap"], "MthCap", key)
    return_available = mth_ret is not None and mth_ret >= -1
    cap_available = mth_cap is not None
    delisting_complete = row["MthDelFlg"] not in {"G", "M"}
    values = {
        "permno": key[0], "yyyymm": key[1], "month": month,
        "mth_cal_dt": mth_cal_dt,
        "mth_prc_dt": as_date(row["MthPrcDt"], "MthPrcDt", key),
        "mth_ret": mth_ret,
        "mth_retx": as_float(row["MthRetx"], "MthRetx", key),
        "mth_cap": mth_cap,
        "mth_prev_cap": as_float(row["MthPrevCap"], "MthPrevCap", key),
        "mth_prc": as_float(row["MthPrc"], "MthPrc", key),
        "mth_prev_prc": as_float(row["MthPrevPrc"], "MthPrevPrc", key),
        "mth_prev_dt": as_date(row["MthPrevDt"], "MthPrevDt", key),
        "vwretd": as_float(row["vwretd"], "vwretd", key),
        "primary_exch": row["PrimaryExch"],
        "security_type": row["SecurityType"],
        "security_sub_type": row["SecuritySubType"],
        "share_type": row["ShareType"], "issuer_type": row["IssuerType"],
        "us_inc_flg": row["USIncFlg"],
        "conditional_type": row["ConditionalType"],
        "trading_status_flg": row["TradingStatusFlg"],
        "sec_info_start_dt": as_date(row["SecInfoStartDt"], "SecInfoStartDt", key),
        "sec_info_end_dt": as_date(row["SecInfoEndDt"], "SecInfoEndDt", key),
        "mth_ret_flg": row["MthRetFlg"], "mth_del_flg": row["MthDelFlg"],
        "mth_comp_flg": row["MthCompFlg"],
        "mth_comp_sub_flg": row["MthCompSubFlg"],
        "return_available": return_available,
        "return_usable": return_available and delisting_complete,
        "cap_available": cap_available,
        "cap_usable": cap_available and mth_cap > 0,
        "delisting_return_complete": delisting_complete,
        "classification_resolved_by_date": resolved_by_date,
        "source_duplicate_rows": duplicate_rows,
    }
    for name, value in values.items():
        batch[name].append(value)


def write_batch(writer, batch):
    if batch["permno"]:
        writer.write_table(pa.Table.from_pydict(batch, schema=SCHEMA))


def build_dataset(source: Path, temporary_output: Path):
    counts, exchanges, delisting, return_flags, statuses = (Counter() for _ in range(5))
    month_counts, stocks = Counter(), set()
    previous_key = None
    batch = empty_batch()
    with source.open(newline="", encoding="utf-8-sig") as stream, pq.ParquetWriter(
        temporary_output, SCHEMA, compression="zstd", use_dictionary=True
    ) as writer:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        required = {
            "PERMNO", "YYYYMM", "MthCalDt", "MthPrcDt", "vwretd",
            *CORE_FIELDS, *CLASS_FIELDS,
        }
        if not columns or not required.issubset(columns):
            raise DataValidationError(
                f"Missing required columns: {sorted(required.difference(columns or []))}"
            )
        if len(columns) != len(set(columns)):
            raise DataValidationError("Duplicate CSV column names")
        nondis = {column for column in columns if not column.startswith("Dis")}
        grouped = groupby(
            reader, key=lambda row: (int(row["PERMNO"]), int(row["YYYYMM"]))
        )
        for key, iterator in grouped:
            rows = list(iterator)
            counts["raw_rows"] += len(rows)
            counts["unique_stock_months"] += 1
            if previous_key is not None and key <= previous_key:
                raise DataValidationError(f"Out-of-order/noncontiguous key: {key}")
            previous_key = key
            for row in rows:
                month = as_month(row["YYYYMM"], key)
                calendar_date = as_date(row["MthCalDt"], "MthCalDt", key)
                if calendar_date is None or (calendar_date.year, calendar_date.month) != (
                    month.year, month.month
                ):
                    raise DataValidationError(f"YYYYMM/MthCalDt mismatch for {key}")
            differing = {
                field for field in columns if len({row[field] for row in rows}) > 1
            }
            core_conflicts = differing.intersection(CORE_FIELDS)
            if core_conflicts:
                raise DataValidationError(
                    f"Core duplicate conflict for {key}: {sorted(core_conflicts)}"
                )
            records = {class_tuple(row) for row in rows}
            if len(rows) > 1:
                counts["duplicate_groups"] += 1
                counts["duplicate_excess_rows"] += len(rows) - 1
                if len(records) > 1:
                    counts["classification_conflict_groups"] += 1
                    eligibility = {
                        is_common_stock(row) and row["PrimaryExch"] in {"N", "A"}
                        for row in rows
                    }
                    if len(eligibility) > 1:
                        counts["eligibility_conflict_groups"] += 1
                elif not differing.intersection(nondis):
                    counts["distribution_only_groups"] += 1
                else:
                    raise DataValidationError(
                        f"Unexpected non-distribution conflict for {key}: "
                        f"{sorted(differing.intersection(nondis))}"
                    )
            selected, resolved, selected_rows = choose_record(rows, key)
            if resolved:
                counts["history_conflicts_resolved_by_date"] += 1
            selected_conflicts = {
                field for field in nondis
                if len({row[field] for row in selected_rows}) > 1
            }
            if selected_conflicts:
                raise DataValidationError(
                    f"Selected-history conflict for {key}: {sorted(selected_conflicts)}"
                )
            exchanges[selected["PrimaryExch"]] += 1
            if selected["PrimaryExch"] not in {"N", "A"}:
                counts["excluded_other_exchange"] += 1
                continue
            counts["nyse_amex_stock_months"] += 1
            if not is_common_stock(selected):
                counts["excluded_non_common_stock"] += 1
                continue
            counts["processed_rows"] += 1
            stocks.add(key[0]); month_counts[key[1]] += 1
            delisting[selected["MthDelFlg"]] += 1
            return_flags[selected["MthRetFlg"]] += 1
            statuses[(selected["ConditionalType"], selected["TradingStatusFlg"])] += 1
            raw_return, raw_cap = selected["MthRet"].strip(), selected["MthCap"].strip()
            if not raw_return:
                counts["missing_return"] += 1
            else:
                value = as_float(raw_return, "MthRet", key)
                counts["return_below_minus_one"] += value < -1
                counts["return_above_500_percent"] += value > 5
            if not raw_cap:
                counts["missing_cap"] += 1
            else:
                counts["nonpositive_cap"] += float(raw_cap) <= 0
            append_row(batch, selected, key, resolved, len(rows))
            if len(batch["permno"]) >= BATCH_SIZE:
                write_batch(writer, batch); batch = empty_batch()
        write_batch(writer, batch)
    counts["unique_stocks"] = len(stocks)
    counts["months"] = len(month_counts)
    counts["min_month_count"] = min(month_counts.values())
    counts["max_month_count"] = max(month_counts.values())
    return {
        "counts": counts, "exchange_counts": exchanges,
        "delisting_counts": delisting, "return_flag_counts": return_flags,
        "status_counts": statuses,
    }


def signature(path: Path):
    stat = path.stat(); digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest.hexdigest()}


def validate_parquet(path: Path):
    metadata = pq.ParquetFile(path).metadata
    keys = pq.read_table(path, columns=["permno", "yyyymm"])
    permnos = keys.column("permno").to_pylist()
    months = keys.column("yyyymm").to_pylist()
    unique_keys = len(set(zip(permnos, months)))
    result = {
        "rows": metadata.num_rows, "unique_keys": unique_keys,
        "duplicate_keys": metadata.num_rows - unique_keys,
        "stocks": len(set(permnos)), "months": len(set(months)),
        "columns": metadata.num_columns, "row_groups": metadata.num_row_groups,
    }
    expected = {**EXPECTED, "unique_keys": EXPECTED["rows"], "duplicate_keys": 0}
    for name, value in expected.items():
        if result[name] != value:
            raise DataValidationError(
                f"Expected {name}={value:,}; found {result[name]:,}"
            )
    return result


def write_report(path, source, output, results, validation, raw_signature):
    c = results["counts"]; d = results["delisting_counts"]
    rf = results["return_flag_counts"]; st = results["status_counts"]
    lines = [
        "STAGE 2 SAMPLE-CONSTRUCTION REPORT", "", "STATUS",
        "Completed and validated. No momentum portfolios were constructed.",
        f"Raw input: {source}", f"Processed output: {output}",
        f"Raw SHA-256: {raw_signature['sha256']}",
        "Raw file unchanged during processing: yes", "", "SAMPLE WATERFALL",
        f"Raw rows: {c['raw_rows']:,}",
        f"Unique stock-months after duplicate resolution: {c['unique_stock_months']:,}",
        f"Excluded other exchanges: {c['excluded_other_exchange']:,}",
        f"NYSE/NYSE American stock-months: {c['nyse_amex_stock_months']:,}",
        f"Excluded non-common-stock classifications: {c['excluded_non_common_stock']:,}",
        f"Final processed stock-months: {c['processed_rows']:,}", "",
        "DUPLICATE RESOLUTION",
        f"Duplicate groups: {c['duplicate_groups']:,}",
        f"Excess source rows: {c['duplicate_excess_rows']:,}",
        f"Distribution-only groups: {c['distribution_only_groups']:,}",
        f"Historical-classification conflicts: {c['classification_conflict_groups']:,}",
        f"Resolved uniquely by effective date: {c['history_conflicts_resolved_by_date']:,}",
        f"Eligibility-changing conflicts: {c['eligibility_conflict_groups']:,}",
        "Core monthly conflicts: 0", "Ambiguous histories: 0",
        "Unexpected key/non-distribution conflicts: 0", "",
        "PRESERVED DATA AND USABILITY FLAGS",
        f"Missing returns preserved: {c['missing_return']:,}",
        f"Missing capitalization preserved: {c['missing_cap']:,}",
        f"Returns below -100%: {c['return_below_minus_one']:,}",
        f"Returns above +500% preserved: {c['return_above_500_percent']:,}",
        f"Nonpositive capitalization: {c['nonpositive_cap']:,}",
        "return_available: nonmissing simple return >= -1.",
        "return_usable: return_available and MthDelFlg not in {G, M}.",
        "cap_available: nonmissing; cap_usable: nonmissing and > 0.",
        "No values were imputed, trimmed, or winsorized.", "",
        "DELISTING AND RETURN FLAGS",
        "MthDelFlg: " + ", ".join(f"{k}={v:,}" for k, v in sorted(d.items())),
        "MthRetFlg: " + ", ".join(f"{k}={v:,}" for k, v in sorted(rf.items())),
        "A/P/V already include delisting returns; G/M are retained but marked incomplete.",
        "", "TRADING STATUS",
        ", ".join(f"{k[0]}/{k[1]}={v:,}" for k, v in sorted(st.items())),
        "No trading-status exclusion was applied.", "", "DATE CONVENTIONS",
        "yyyymm is the integer source month key.",
        "month is the first calendar day for stable monthly joins.",
        "mth_cal_dt retains the actual CRSP month-end trading date.",
        "Classification is selected on MthPrcDt, falling back to MthCalDt.",
        "", "VALIDATION",
        f"Rows: {validation['rows']:,} (expected {EXPECTED['rows']:,})",
        f"Distinct PERMNOs: {validation['stocks']:,} (expected {EXPECTED['stocks']:,})",
        f"Distinct months: {validation['months']:,} (expected {EXPECTED['months']:,})",
        f"Duplicate PERMNO-month keys: {validation['duplicate_keys']:,}",
        f"Columns: {validation['columns']:,}",
        f"Parquet row groups: {validation['row_groups']:,}",
        f"Monthly stock-count range: {c['min_month_count']:,} to {c['max_month_count']:,}",
        "", "ASSUMPTIONS AND LIMITATIONS",
        "The modern 2000-2025 sample cannot reproduce the paper's 1965-1989 period.",
        "The common-stock rule maps CIZ fields to legacy CRSP share codes 10/11.",
        "Distribution rows are collapsed because MthRet already aggregates distributions.",
        "Six G/M delisting observations need an explicit portfolio-stage decision.",
        "The daily file still needs full preparation for skip-week and beta analyses.",
        "Formation/holding eligibility will be determined dynamically later.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "02_Monthly_Stocks_and_Factors/data/monthly_stock.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "processed/monthly_stock_prepared.parquet")
    parser.add_argument("--report", type=Path, default=ROOT / "outputs/logs/sample_construction_report.txt")
    args = parser.parse_args()
    source, output, report = args.input.resolve(), args.output.resolve(), args.report.resolve()
    output.parent.mkdir(parents=True, exist_ok=True); report.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp"); temporary.unlink(missing_ok=True)
    before = signature(source)
    try:
        results = build_dataset(source, temporary)
        after = signature(source)
        if before != after:
            raise DataValidationError("Raw input changed during processing")
        validation = validate_parquet(temporary)
        temporary.replace(output)
        write_report(report, source, output, results, validation, before)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    report.with_name("sample_construction_review.txt").unlink(missing_ok=True)
    print(f"Processed {validation['rows']:,} stock-months.")
    print(f"Distinct PERMNOs: {validation['stocks']:,}")
    print(f"Distinct months: {validation['months']:,}")
    print(f"Duplicate keys: {validation['duplicate_keys']:,}")
    print(f"Raw SHA-256 unchanged: {before['sha256']}")
    print(f"Dataset: {output}"); print(f"Report: {report}")


if __name__ == "__main__":
    main()
