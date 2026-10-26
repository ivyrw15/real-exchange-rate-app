"""
Real Exchange Rate (RER) app using IMF SDMX 3.0 data.

Definitions
    X_i(t) : IMF exchange rate, units of country i currency per 1 USD (period average)
    S(t)   : units of Country 2 currency per 1 unit of Country 1 currency = X_2(t) / X_1(t)
    P1, P2 : monthly CPI index levels for Country 1 and Country 2
    RER(t) = S(t) * P1(t) / P2(t)
    RER*   = average of RER(t) over valid months in the selected sample
    Real Index(t) = 100 * RER(t) / RER*
"""

import io
import xml.etree.ElementTree as ET

import pandas as pd
import plotly.graph_objects as go
import pycountry
import requests
import streamlit as st
from plotly.subplots import make_subplots

BASE_URL = "https://api.imf.org/external/sdmx/3.0"
AGENCY = "IMF.STA"
CPI_FLOW = "CPI"
ER_FLOW = "ER"
START_PERIOD = pd.Timestamp("1976-01-01")
EURO_AREA_CODE = "G163"

st.set_page_config(page_title="Real Exchange Rate App", layout="wide")


# ---------------------------------------------------------------------------
# Country list
# ---------------------------------------------------------------------------
@st.cache_data
def country_options() -> dict:
    """Return {display label: ISO3 code}. Euro area listed first."""
    options = {"Euro area (G163)": EURO_AREA_CODE}
    countries = sorted(
        ((c.name, c.alpha_3) for c in pycountry.countries), key=lambda x: x[0]
    )
    for name, code in countries:
        options[f"{name} ({code})"] = code
    return options


# ---------------------------------------------------------------------------
# IMF data retrieval
# ---------------------------------------------------------------------------
def _headers(accept: str) -> dict:
    headers = {
        "Accept": accept,
        "User-Agent": "real-exchange-rate-app/1.0 (Streamlit)",
        "Cache-Control": "no-cache",
    }
    # Optional: only used if a key is added in Streamlit secrets.
    try:
        key = st.secrets.get("IMF_API_KEY", None)
    except Exception:
        key = None
    if key:
        headers["Ocp-Apim-Subscription-Key"] = key
    return headers


def _parse_period(value: str) -> pd.Timestamp:
    """Handle IMF monthly formats such as 1976-M01, 1976-01, 1976M01."""
    v = str(value).strip().replace("-M", "-").replace("M", "-")
    v = v.replace("--", "-")
    return pd.to_datetime(v, format="%Y-%m", errors="coerce")


def _parse_json(payload: dict) -> pd.Series:
    """Parse SDMX-JSON (1.0 or 2.0) into a Series indexed by month."""
    data = payload.get("data", payload)
    datasets = data.get("dataSets", [])
    if not datasets:
        return pd.Series(dtype=float)

    structure = data.get("structures", [data.get("structure", {})])
    if isinstance(structure, list):
        structure = structure[0] if structure else {}
    obs_dims = structure.get("dimensions", {}).get("observation", [])
    time_values = []
    for dim in obs_dims:
        if dim.get("id") == "TIME_PERIOD":
            time_values = [v.get("value", v.get("id")) for v in dim.get("values", [])]
            break
    if not time_values and obs_dims:
        time_values = [v.get("value", v.get("id")) for v in obs_dims[0].get("values", [])]

    records = {}
    for series in datasets[0].get("series", {}).values():
        for idx, obs in series.get("observations", {}).items():
            pos = int(str(idx).split(":")[0])
            if pos >= len(time_values) or not obs or obs[0] is None:
                continue
            date = _parse_period(time_values[pos])
            try:
                records[date] = float(obs[0])
            except (TypeError, ValueError):
                continue
    return pd.Series(records, dtype=float).sort_index()


def _parse_xml(text: str) -> pd.Series:
    """Parse SDMX-ML (structure-specific or generic) into a Series."""
    root = ET.fromstring(text)
    records = {}
    for el in root.iter():
        if not el.tag.endswith("Obs"):
            continue
        period = el.attrib.get("TIME_PERIOD")
        value = el.attrib.get("OBS_VALUE")
        if period is None or value is None:
            # Generic format: child elements hold the values
            for child in el:
                tag = child.tag.split("}")[-1]
                if tag == "ObsDimension":
                    period = child.attrib.get("value")
                elif tag == "ObsValue":
                    value = child.attrib.get("value")
        if period is None or value is None:
            continue
        try:
            records[_parse_period(period)] = float(value)
        except ValueError:
            continue
    return pd.Series(records, dtype=float).sort_index()


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def fetch_series(flow: str, key: str) -> pd.Series:
    """Download one monthly IMF series. Returns an empty Series if unavailable."""
    url = f"{BASE_URL}/data/dataflow/{AGENCY}/{flow}/+/{key}"
    params = {
        "dimensionAtObservation": "TIME_PERIOD",
        "attributes": "dsd",
        "measures": "all",
    }
    attempts = [
        "application/vnd.sdmx.data+json;version=2.0.0",
        "application/json",
        "application/xml",
    ]
    last_error = None
    for accept in attempts:
        try:
            r = requests.get(url, params=params, headers=_headers(accept), timeout=60)
            if r.status_code == 404:
                return pd.Series(dtype=float)
            r.raise_for_status()
            body = r.text.strip()
            if body.startswith("{"):
                s = _parse_json(r.json())
            else:
                s = _parse_xml(body)
            s = s[s.index.notna()]
            s = s[~s.index.duplicated(keep="last")]
            return s[s.index >= START_PERIOD]
        except Exception as exc:  # try the next format
            last_error = exc
    raise RuntimeError(f"IMF request failed for {flow}/{key}: {last_error}")


def get_cpi(iso3: str) -> pd.Series:
    return fetch_series(CPI_FLOW, f"{iso3}.CPI._T.IX.M")


def get_fx_per_usd(iso3: str) -> pd.Series:
    """Local currency units per USD. The US is 1 by definition."""
    if iso3 == "USA":
        idx = pd.date_range(START_PERIOD, pd.Timestamp.today().normalize(), freq="MS")
        return pd.Series(1.0, index=idx)
    return fetch_series(ER_FLOW, f"{iso3}.XDC_USD.PA_RT.M")


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------
def build_panel(c1: str, c2: str) -> pd.DataFrame:
    x1 = get_fx_per_usd(c1)
    x2 = get_fx_per_usd(c2)
    p1 = get_cpi(c1)
    p2 = get_cpi(c2)

    df = pd.concat(
        {
            "X1_LCU1_per_USD": x1,
            "X2_LCU2_per_USD": x2,
            "P1_CPI": p1,
            "P2_CPI": p2,
        },
        axis=1,
    ).sort_index()
    df.index.name = "month"

    # S(t) = units of Country 2 currency per 1 unit of Country 1 currency
    df["S_C2_per_C1"] = df["X2_LCU2_per_USD"] / df["X1_LCU1_per_USD"]
    df["RER"] = df["S_C2_per_C1"] * df["P1_CPI"] / df["P2_CPI"]

    valid = (
        df[["S_C2_per_C1", "P1_CPI", "P2_CPI"]].notna().all(axis=1)
        & (df["S_C2_per_C1"] > 0)
        & (df["P1_CPI"] > 0)
        & (df["P2_CPI"] > 0)
    )
    df["valid_month"] = valid
    df.loc[~valid, "RER"] = float("nan")
    return df


def apply_sample(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp):
    sample = df.loc[(df.index >= start) & (df.index <= end)].copy()
    rer_star = sample.loc[sample["valid_month"], "RER"].astype(float).mean()
    sample["RER_star"] = rer_star
    sample["Real_Index"] = 100 * sample["RER"].astype(float) / rer_star
    return sample, rer_star


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("Real exchange rate explorer")
st.write(
    "Choose two economies to compare. S(t) is units of Country 2 currency per "
    "one unit of Country 1 currency, and RER(t) = S(t) x P1(t) / P2(t). "
    "Data: IMF CPI and ER dataflows (monthly, from January 1976 where available)."
)

opts = country_options()
labels = list(opts.keys())
default_1 = next(l for l in labels if opts[l] == "GBR")
default_2 = next(l for l in labels if opts[l] == "USA")

col_a, col_b = st.columns(2)
with col_a:
    label_1 = st.selectbox("Country 1", labels, index=labels.index(default_1))
with col_b:
    label_2 = st.selectbox("Country 2", labels, index=labels.index(default_2))

c1, c2 = opts[label_1], opts[label_2]
name_1 = label_1.rsplit(" (", 1)[0]
name_2 = label_2.rsplit(" (", 1)[0]

if c1 == c2:
    st.warning("Choose two different countries.")
    st.stop()

try:
    with st.spinner("Downloading IMF data..."):
        panel = build_panel(c1, c2)
except Exception as exc:
    st.error(
        f"Could not download IMF data. Details: {exc}. "
        "The IMF API may be temporarily unavailable; try again in a minute."
    )
    st.stop()

# Report which inputs are missing
missing = [
    name
    for name, col in [
        (f"{name_1} exchange rate", "X1_LCU1_per_USD"),
        (f"{name_2} exchange rate", "X2_LCU2_per_USD"),
        (f"{name_1} CPI", "P1_CPI"),
        (f"{name_2} CPI", "P2_CPI"),
    ]
    if panel[col].dropna().empty
]
if missing:
    st.error("The IMF has no monthly data for: " + ", ".join(missing) + ". Choose other countries.")
    st.stop()

valid_months = panel.index[panel["valid_month"]]
if len(valid_months) == 0:
    st.error("The four input series never overlap in the same month. Choose other countries.")
    st.stop()

month_options = list(pd.date_range(valid_months.min(), valid_months.max(), freq="MS"))
start, end = st.select_slider(
    "Sample period",
    options=month_options,
    value=(month_options[0], month_options[-1]),
    format_func=lambda d: d.strftime("%Y-%m"),
)

sample, rer_star = apply_sample(panel, start, end)
n_valid = int(sample["valid_month"].sum())
if n_valid == 0 or pd.isna(rer_star):
    st.error("No valid months in the selected sample. Widen the date range.")
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric("Valid months in sample", f"{n_valid}")
m2.metric("RER* (sample average)", f"{rer_star:,.4f}")
latest = sample.loc[sample["valid_month"]].iloc[-1]
m3.metric(f"Latest S ({c2} per {c1})", f"{latest['S_C2_per_C1']:,.4f}")

if n_valid < len(sample):
    st.caption(
        f"{len(sample) - n_valid} month(s) in the sample are missing at least one input "
        "and are excluded from RER*."
    )

view = st.radio("Chart", ["Real", "Nominal", "Both"], horizontal=True)

real_name = "Real index (sample avg = 100)"
nom_name = f"Nominal S: {name_2} currency per 1 {name_1} currency"
plot_df = sample.loc[sample["valid_month"]]

if view == "Both":
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(x=plot_df.index, y=plot_df["Real_Index"], name=real_name),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(x=plot_df.index, y=plot_df["S_C2_per_C1"], name=nom_name),
        secondary_y=True,
    )
    fig.update_yaxes(title_text="Real index", secondary_y=False)
    fig.update_yaxes(title_text=f"S ({c2} per {c1})", secondary_y=True)
elif view == "Real":
    fig = go.Figure(go.Scatter(x=plot_df.index, y=plot_df["Real_Index"], name=real_name))
    fig.add_hline(y=100, line_dash="dot")
    fig.update_yaxes(title_text="Real index (sample avg = 100)")
else:
    fig = go.Figure(go.Scatter(x=plot_df.index, y=plot_df["S_C2_per_C1"], name=nom_name))
    fig.update_yaxes(title_text=f"S ({c2} per {c1})")

fig.update_layout(
    hovermode="x unified",
    legend=dict(orientation="h", y=-0.2),
    margin=dict(t=30),
    title=f"{name_1} vs {name_2}",
)
st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Table and CSV export
# ---------------------------------------------------------------------------
export = sample.copy()
export.insert(0, "country_1", c1)
export.insert(1, "country_2", c2)
export = export.reset_index()
export["month"] = export["month"].dt.strftime("%Y-%m")
export = export[
    [
        "month",
        "country_1",
        "country_2",
        "X1_LCU1_per_USD",
        "X2_LCU2_per_USD",
        "S_C2_per_C1",
        "P1_CPI",
        "P2_CPI",
        "RER",
        "RER_star",
        "Real_Index",
        "valid_month",
    ]
]

with st.expander("Show data"):
    st.dataframe(export, use_container_width=True, hide_index=True)

buf = io.StringIO()
export.to_csv(buf, index=False)
st.download_button(
    "Download CSV",
    data=buf.getvalue(),
    file_name=f"rer_{c1}_{c2}_{start:%Y%m}_{end:%Y%m}.csv",
    mime="text/csv",
)

st.caption(
    "Notes: IMF exchange rates are period averages in local currency per USD, so "
    "S = X2 / X1. CPI base years differ by country, which only rescales RER and "
    "cancels in the real index. Coverage varies by country and some series have gaps."
)
