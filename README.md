# Real Exchange Rate App

A Streamlit app that computes the bilateral real exchange rate (RER) between any two IMF economies (including the Euro area, IMF code G163) using monthly IMF data from January 1976 where available.

## Economics

- X_i(t): IMF exchange rate, units of country i currency per 1 USD (period average, key `{ISO3}.XDC_USD.PA_RT.M`)
- S(t): units of Country 2 currency per 1 unit of Country 1 currency, computed as X_2(t) / X_1(t)
- P1(t), P2(t): monthly CPI index levels (key `{ISO3}.CPI._T.IX.M`)
- RER(t) = S(t) x P1(t) / P2(t)
- RER*: average of RER(t) over valid months in the user-selected sample
- Real Index(t) = 100 x RER(t) / RER*

A month is valid only when S, P1 and P2 are all available and positive. RER* is recalculated whenever the sample dates change. Nominal S(t) is plotted in actual currency units, not normalized. The US exchange rate is set to 1 USD per USD.

Example: Country 1 = United Kingdom, Country 2 = United States gives S in USD per GBP.

## Features

- Country 1 and Country 2 selectors (all ISO3 economies plus the Euro area)
- Month range slider for the sample period
- Real, Nominal, or Both charts (Both uses separate y-axes)
- CSV download of all observations and calculations in the selected sample

## Data source

IMF SDMX 3.0 API: https://api.imf.org/external/sdmx/3.0 (dataflows `IMF.STA/CPI` and `IMF.STA/ER`). No API key is normally required. Coverage varies by country and some series have gaps.

## Run

Deployed on Streamlit Community Cloud with Python 3.12. To run locally:

```
pip install -r requirements.txt
streamlit run app.py
```

## Files

- `app.py`: the Streamlit app
- `requirements.txt`: Python dependencies
- `README.md`: this file
