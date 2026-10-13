"""Read-only monthly CSV audit. Run from any directory with Python 3.10+.

Blank/whitespace-only fields are counted as missing. Other tokens are retained;
non-numeric tokens in numeric fields are reported separately. SQLite uses temporary
disk storage for exact duplicate checks and percentiles. No sample filters applied.
"""
from pathlib import Path
from collections import Counter, defaultdict
from datetime import date
import argparse
import csv
import json
import math
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def write_report(path, summary, schema, stats, coverage, categories):
    """Keep the complete audit in one readable file; no row-level exports."""
    lines = ['MONTHLY STOCK DATA INSPECTION', '']
    lines.extend(f'{key}: {value}' for key, value in summary.items()
                 if key != 'issue_counts')
    lines.extend(['', 'QUALITY FLAGS (counts may overlap)'])
    flag_names = ('missing_permno', 'malformed_permno', 'missing_date',
                  'unparseable_date', 'MthRet_missing',
                  'MthRet_nonnumeric_or_nonfinite', 'return_below_minus_one',
                  'MthCap_missing', 'MthCap_nonnumeric_or_nonfinite',
                  'nonpositive_cap')
    lines.extend(f'{key}: {summary["issue_counts"].get(key, 0):,}'
                 for key in flag_names)
    lines.extend(['', 'COLUMN TYPES AND MISSING VALUES',
                  'Column | Observed token types (counts) | Missing count | Missing %'])
    for row in schema:
        lines.append(f'{row["column"]} | {row["observed_types"]} | '
                     f'{row["missing_count"]:,} | {row["missing_percent"]:.4f}')
    lines.extend(['', 'DESCRIPTIVE STATISTICS',
                  'Returns are decimals; capitalization is in unverified source units.',
                  'All finite numeric records are included, including duplicate/flagged rows.'])
    for row in stats:
        lines.append(row['column'])
        lines.extend(f'  {key}: {value}' for key, value in row.items()
                     if key != 'column')
    lines.extend(['', 'EXCHANGE, SECURITY AND RETURN FLAGS',
                  'Counts are records, including duplicates; code meanings need verification.'])
    for column, counts in categories.items():
        lines.append(f'{column}: ' + ', '.join(f'{value}={count:,}'
                                              for value, count in sorted(counts.items())))
    lines.extend(['', 'MONTHLY COVERAGE',
                  'Month | Records | Unique stocks with valid returns'])
    lines.extend(f'{row["month"]} | {row["record_count"]:,} | '
                 f'{row["unique_stocks_with_valid_return"]:,}' for row in coverage)
    lines.extend(['', 'INTERPRETATION AND LIMITS',
                  'No data cleaning, imputation, sample selection or portfolio construction performed.',
                  'Resolve duplicate keys and historical classification timing before analysis.',
                  'Preserve missing values and valid pre-delisting observations.',
                  'Missing returns may affect formation/holding eligibility; missing capitalization may affect size sorts.',
                  'Do not automatically remove extreme positive returns or valid -100% returns.',
                  'Verify exchange/security codes, capitalization units and delisting-return treatment.',
                  'Monthly coverage alone does not establish consecutive formation histories or eligible holding returns.'])
    if summary['earliest_date'] and summary['earliest_date'] > '1989-12-31':
        lines.append('This file contains no observations in the original 1965-1989 evaluation period.')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def inspect(source, output):
    output.mkdir(parents=True, exist_ok=True)
    before = source.stat()
    missing, types, categories = Counter(), defaultdict(Counter), defaultdict(Counter)
    issues, months, stocks = Counter(), defaultdict(set), set()
    month_rows = Counter()
    dates = []
    moments = {name: [0, 0.0, 0.0, math.inf, -math.inf] for name in ('MthRet', 'MthCap')}
    selected = ('PrimaryExch', 'SecurityType', 'SecuritySubType', 'ShareType', 'MthRetFlg', 'MthDelFlg')
    with tempfile.TemporaryDirectory(prefix='monthly_audit_') as temp:
        db = sqlite3.connect(str(Path(temp) / 'audit.sqlite'))
        db.execute('PRAGMA temp_store=FILE')
        db.execute('CREATE TABLE observations (permno TEXT, dt TEXT, ret REAL, cap REAL)')
        batch = []
        with source.open(newline='', encoding='utf-8-sig') as stream:
            reader = csv.DictReader(stream)
            columns = reader.fieldnames
            required = {'PERMNO', 'MthCalDt', 'MthRet', 'MthCap'}
            if not columns or not required.issubset(columns) or len(columns) != len(set(columns)):
                raise ValueError('Missing required fields or duplicate column headers')
            for total, row in enumerate(reader, 1):
                if None in row or any(v is None for v in row.values()):
                    raise ValueError(f'Malformed CSV record {total}')
                for key, raw in row.items():
                    value = raw.strip()
                    if not value:
                        missing[key] += 1
                    else:
                        numeric = number(value)
                        kind = 'integer-like' if numeric is not None and numeric.is_integer() else 'float-like' if numeric is not None else 'text'
                        types[key][kind] += 1
                for key in selected:
                    if key in row:
                        categories[key][row[key].strip() or '<missing>'] += 1
                permno, dt = row['PERMNO'].strip(), row['MthCalDt'].strip()
                flags = []
                if permno:
                    stocks.add(permno)
                    if not permno.isdigit():
                        flags.append('malformed_permno')
                else:
                    flags.append('missing_permno')
                try:
                    parsed = date.fromisoformat(dt)
                    dates.append(parsed) if len(dates) < 2 else None
                    dates = [min(dates[0], parsed), max(dates[-1], parsed)]
                    month = parsed.strftime('%Y-%m')
                    month_rows[month] += 1
                except ValueError:
                    month = None
                    flags.append('missing_date' if not dt else 'unparseable_date')
                values = {}
                for key in moments:
                    raw = row[key].strip()
                    val = number(raw) if raw else None
                    values[key] = val
                    if val is None:
                        flags.append(f'{key}_missing' if not raw else f'{key}_nonnumeric_or_nonfinite')
                    else:
                        state = moments[key]
                        state[0] += 1
                        delta = val - state[1]
                        state[1] += delta / state[0]
                        state[2] += delta * (val - state[1])
                        state[3], state[4] = min(state[3], val), max(state[4], val)
                ret, cap = values['MthRet'], values['MthCap']
                if ret is not None and ret < -1:
                    flags.append('return_below_minus_one')
                if cap is not None and cap <= 0:
                    flags.append('nonpositive_cap')
                # Valid for this audit means finite numeric simple return >= -1.
                # This is not a statement of final portfolio eligibility.
                if month and permno and ret is not None and ret >= -1:
                    months[month].add(permno)
                issues.update(flags)
                batch.append((permno or None, dt or None, ret, cap))
                if len(batch) >= 50000:
                    db.executemany('INSERT INTO observations VALUES (?,?,?,?)', batch)
                    db.commit()
                    batch.clear()
                    if total % 500000 == 0:
                        print(f'Inspected {total:,} records', flush=True)
            total = locals().get('total', 0)
        db.executemany('INSERT INTO observations VALUES (?,?,?,?)', batch)
        db.commit()
        db.execute('CREATE INDEX keys ON observations(permno,dt)')
        duplicate_groups = duplicate_rows = duplicate_excess = 0
        for _, _, count in db.execute('SELECT permno,dt,COUNT(*) FROM observations WHERE permno IS NOT NULL AND dt IS NOT NULL GROUP BY permno,dt HAVING COUNT(*)>1'):
            duplicate_groups += 1
            duplicate_rows += count
            duplicate_excess += count - 1
        stats = []
        for key, field in [('MthRet', 'ret'), ('MthCap', 'cap')]:
            n, mean, m2, minimum, maximum = moments[key]
            db.execute(f'CREATE INDEX idx_{field} ON observations({field})')
            result = dict(column=key, count=n, mean=mean if n else None, std=math.sqrt(m2/(n-1)) if n > 1 else None, min=minimum if n else None, max=maximum if n else None)
            for label, p in [('p01', .01), ('p25', .25), ('p50', .5), ('p75', .75), ('p99', .99)]:
                if n:
                    position = (n-1)*p
                    lo, hi = math.floor(position), math.ceil(position)
                    vals = [r[0] for r in db.execute(f'SELECT {field} FROM observations WHERE {field} IS NOT NULL ORDER BY {field} LIMIT ? OFFSET ?', (hi-lo+1, lo))]
                    result[label] = vals[0] + (position-lo)*(vals[-1]-vals[0])
                else:
                    result[label] = None
            stats.append(result)
        db.close()
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError('Input file changed during inspection')
    schema = [dict(column=c, observed_types=json.dumps(dict(types[c]), sort_keys=True), missing_count=missing[c], missing_percent=100*missing[c]/total if total else 0) for c in columns]
    coverage = [dict(month=m, record_count=month_rows[m],
                     unique_stocks_with_valid_return=len(months[m]))
                for m in sorted(month_rows)]
    summary = dict(input=str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source), input_bytes=before.st_size, rows=total, columns=len(columns), earliest_date=str(dates[0]) if dates else None, latest_date=str(dates[-1]) if dates else None, unique_nonblank_permno=len(stocks), duplicate_groups=duplicate_groups, rows_in_duplicate_groups=duplicate_rows, duplicate_excess_records=duplicate_excess, issue_counts=dict(issues), missing_definition='blank or whitespace-only; no special CRSP missing-code mapping assumed', valid_return_definition='finite numeric value >= -1; missing stock IDs/dates excluded from monthly counts', descriptive_statistics_scope='all finite numeric observations, including flagged values; sample standard deviation; exact linearly interpolated percentiles', dtype_definition='CSV has no intrinsic dtype; observed token types reported across the entire file', input_unchanged_size_and_mtime=True)
    report = output / 'inspection_report.txt'
    write_report(report, summary, schema, stats, coverage, categories)
    print(f'Rows: {total:,}; columns: {len(columns)}; stocks: {len(stocks):,}')
    print(f'Dates: {summary["earliest_date"]} to {summary["latest_date"]}')
    print(f'Duplicate groups: {duplicate_groups:,}; excess records: {duplicate_excess:,}')
    print(f'Complete inspection report: {report}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT/'02_Monthly_Stocks_and_Factors/data/monthly_stock.csv')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/logs')
    args = parser.parse_args()
    inspect(args.input.resolve(), args.output.resolve())
