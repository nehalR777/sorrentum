# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.15.2
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Description
#
# Analyze trading execution quality.

# %% run_control={"marked": true}
# %load_ext autoreload
# %autoreload 2
import logging

import numpy as np
import pandas as pd

import core.config as cconfig
import core.finance as cofinanc
import core.finance.target_position_df_processing as cftpdp
import core.plotting as coplotti
import core.plotting.execution_stats as cplexsta
import core.statistics as costatis
import dataflow_amp.system.Cx as dtfamsysc
import helpers.hdbg as hdbg
import helpers.henv as henv
import helpers.hpandas as hpandas
import helpers.hprint as hprint
import im_v2.common.universe as ivcu
import oms.broker.ccxt.ccxt_aggregation_functions as obccagfu
import oms.broker.ccxt.ccxt_execution_quality as obccexqu
import oms.broker.ccxt.ccxt_logger as obcccclo
import oms.child_order_quantity_computer.child_order_quantity_computer_instances as ocoqccoqci
import oms.order.order_converter as oororcon

# %%
hdbg.init_logger(verbosity=logging.INFO)

_LOG = logging.getLogger(__name__)

_LOG.info("%s", henv.get_system_signature()[0])

hprint.config_notebook()

# %% [markdown]
# # Config

# %%
config = cconfig.get_config_from_env()
if config:
    # Get config from env when running the notebook via the `run_notebook.py` script, e.g.,
    # in the system reconciliation flow.
    _LOG.info("Using config from env vars")
else:
    system_log_dir = "/shared_data/ecs/test/system_reconciliation/C12a/prod/20240219_150900.20240219_160600/system_log_dir.manual/process_forecasts"
    id_col = "asset_id"
    universe_version = "v7.5"
    vendor = "CCXT"
    mode = "trade"
    test_asset_id = 1464553467
    bar_duration = "3T"
    child_order_execution_freq = "60S"
    use_historical = True
    config_dict = {
        "meta": {"id_col": id_col, "use_historical": use_historical},
        "system_log_dir": system_log_dir,
        "ohlcv_market_data": {
            "vendor": vendor,
            "mode": mode,
            "universe": {
                "universe_version": universe_version,
                "test_asset_id": test_asset_id,
            },
        },
        "execution_parameters": {
            "bar_duration": bar_duration,
            "execution_freq": child_order_execution_freq,
        },
    }
    config = cconfig.Config.from_dict(config_dict)
print(config)

# %% run_control={"marked": true}
# Init the log reader.
log_dir = config.get_and_mark_as_used(("system_log_dir",))
ccxt_log_reader = obcccclo.CcxtLogger(log_dir)


# %%
# Print the Broker config.
try:
    broker_config = ccxt_log_reader.load_broker_config(abort_on_missing_data=True)
    print(hprint.to_pretty_str(broker_config))
    scheduler_type = broker_config["child_order_quantity_computer"]["object_type"]
except FileNotFoundError:
    _LOG.warning(
        "broker_config file not present in %s, assuming Dynamic Scheduling",
        log_dir,
    )
    scheduler_type = "DynamicSchedulingChildOrderQuantityComputer"

# %%
# Print the used Config, if any.
experiment_config = obcccclo.load_config_for_execution_analysis(log_dir)
if experiment_config:
    print(experiment_config)

# %%
# Get the test asset ID from the config.
test_asset_id = config.get_and_mark_as_used(
    ("ohlcv_market_data", "universe", "test_asset_id")
)
id_col = config.get_and_mark_as_used(("meta", "id_col"))

# %%
bar_duration = config.get_and_mark_as_used(
    ("execution_parameters", "bar_duration")
)
exec_freq = config.get_and_mark_as_used(
    ("execution_parameters", "execution_freq")
)
# Initialize scheduler according to it's type.
scheduler = ocoqccoqci.get_child_order_quantity_computer_instance1(scheduler_type)
# Get the range of expected number of child orders.
range_filter = scheduler.get_range_filter(bar_duration, exec_freq)

# %% [markdown]
# # Load and aggregate data

# %% [markdown]
# ## Load CCXT data

# %%
data = ccxt_log_reader.load_all_data(
    convert_to_dataframe=True, abort_on_missing_data=False
)

# %% [markdown]
# ### Order responses

# %%
ccxt_order_response_df = data["ccxt_order_responses"]
ccxt_order_response_df.head(3)

# %%
ccxt_order_response_df.loc[1]["info"]

# %%
ccxt_order_response_df.info()

# %% [markdown]
# ### OMS child orders

# %%
child_order_df = data["oms_child_orders"]
# TODO: group by asset, agg by mean total_vol_to_spread_bps, chart.
child_order_df.head(3)

# %%
child_order_df.info()

# %% [markdown]
# ### CCXT fills (trades)

# %%
fills_df = data["ccxt_trades"]
# Annotate fills with child order wave ID.
fills_df = obccexqu.annotate_fills_df_with_wave_id(fills_df, child_order_df)
fills_df.head(3)

# %%
fills_df.info()

# %% [markdown]
# ### OMS parent orders

# %%
parent_order_df = data["oms_parent_orders"]
parent_order_df.head(3)

# %%
parent_order_df.info()

# %% [markdown]
# ## Aggregate CCXT Data

# %%
bar_duration = config.get_and_mark_as_used(
    ("execution_parameters", "bar_duration")
)
# TODO(Paul): Look into adding tqdm.
# Aggregate order responses by bar.
bar_ccxt_order_aggregation = obccagfu.aggregate_ccxt_orders_by_bar(
    ccxt_order_response_df, bar_duration
)
# Aggregate fills by order.
ccxt_order_fills = obccagfu.aggregate_fills_by_order(fills_df)
# Aggregate fills by bar.
bar_fills = obccagfu.aggregate_fills_by_bar(
    fills_df, bar_duration, groupby_id_col=id_col
)
# Aggregate buy/sell trade prices by bar.
trade_prices = obccagfu.compute_buy_sell_prices_by_bar(
    fills_df, bar_duration, groupby_id_col=id_col
)
# Aggregate OMS child orders.
bar_child_order_aggregation = obccagfu.aggregate_child_limit_orders_by_bar(
    child_order_df, bar_duration
)

# %%
bar_ccxt_order_aggregation.head(3)

# %%
ccxt_order_fills.head(3)

# %%
bar_fills.head(3)

# %%
trade_prices.head(3)

# %%
bar_child_order_aggregation.head(3)

# %%
cofinanc.get_asset_slice(bar_ccxt_order_aggregation, test_asset_id)

# %% [markdown]
# ### Align CCXT orders and fills

# %%
# Get filled and unfilled CCXT orders.
filled_ccxt_orders, unfilled_ccxt_orders = obccexqu.align_ccxt_orders_and_fills(
    ccxt_order_response_df, fills_df
)

# %%
filled_ccxt_orders.head(3)

# %%
unfilled_ccxt_orders.head(3)

# %% [markdown]
# ## Load OHLCV data

# %%
# TODO(Paul): Refine the cuts around the first and last bars.
start_timestamp = bar_fills["first_datetime"].min() - pd.Timedelta(bar_duration)
_LOG.info("start_timestamp=%s", start_timestamp)
end_timestamp = bar_fills["last_datetime"].max() + pd.Timedelta(bar_duration)
_LOG.info("end_timestamp=%s", end_timestamp)

# %%
universe_version = config.get_and_mark_as_used(
    ("ohlcv_market_data", "universe", "universe_version")
)
vendor = config.get_and_mark_as_used(
    (
        "ohlcv_market_data",
        "vendor",
    )
)
mode = config.get_and_mark_as_used(
    (
        "ohlcv_market_data",
        "mode",
    )
)
# Get asset ids.
asset_ids = ivcu.get_vendor_universe_as_asset_ids(universe_version, vendor, mode)
# Get prod `MarketData`.
db_stage = "preprod"
market_data = dtfamsysc.get_Cx_RealTimeMarketData_prod_instance1(
    asset_ids, db_stage
)
# Load and resample OHLCV data.
ohlcv_bars = dtfamsysc.load_and_resample_ohlcv_data(
    market_data,
    start_timestamp,
    end_timestamp,
    bar_duration,
)
hpandas.df_to_str(ohlcv_bars, num_rows=5, log_level=logging.INFO)

# %% [markdown]
# ## Load exchange tick sizes by asset id

# %%
# Create a mapping between binance full symbol and asset_id.
binance_full_symbol_to_asset_id_mapping = dict(
    zip(fills_df.symbol, fills_df.asset_id)
)
# Load exchange markets and restrict to assets traded.
exchange_markets = data["exchange_markets"].loc[
    binance_full_symbol_to_asset_id_mapping.keys()
]
exchange_markets.head(3)

# %%
# Get the minimum tick size per asset.
price_tick_srs = exchange_markets["precision"].apply(lambda x: x["price"])
price_tick_srs = price_tick_srs.apply(lambda x: 10**-x)
# Map index to the asset_id.
price_tick_srs.index = price_tick_srs.index.map(
    binance_full_symbol_to_asset_id_mapping
)
price_tick_srs.head(3)

# %% [markdown]
# # Cross-checks

# %% [markdown]
# ## Basic checks

# %% [markdown]
# - Number of bars and assets
# - Moments
# - Non-submitted orders

# %%
# Verify number of bars in the parent order DF.
number_of_bars = int(
    np.ceil(
        (
            parent_order_df["end_timestamp"].max()
            - parent_order_df["start_timestamp"].min()
        )
        / bar_duration
    )
)
_LOG.info("number of bars=%d", number_of_bars)

# %% run_control={"marked": false}
# Check number of unique asset IDs.
unique_asset_id_count = len(parent_order_df["asset_id"].unique())
_LOG.info("unique asset_id count=%d", unique_asset_id_count)

# %%
# Share counts should change if there are many orders and we are getting fills.
costatis.compute_moments(parent_order_df["curr_num_shares"])

# %%
# Verify that test asset id is present in the CCXT fills.
hdbg.dassert_in(test_asset_id, fills_df[id_col].unique())

# %%
inconsistent_order_num_df = bar_ccxt_order_aggregation["order_count"].stack()[
    ~bar_ccxt_order_aggregation["order_count"].stack().isin(range_filter)
]
if not inconsistent_order_num_df.empty:
    child_order_df[
        (
            child_order_df["creation_timestamp"]
            > inconsistent_order_num_df.index.min()[0]
        )
        & (
            child_order_df["creation_timestamp"]
            < inconsistent_order_num_df.index.max()[0]
        )
        & (child_order_df[id_col].isin(inconsistent_order_num_df.index[1]))
    ]

# %%
# The number of child orders can be greater than the number of order responses
# if the child order was not accepted by the exchange.
# In this case the child order should have an error message (see cells below).
# If some non-submitted orders don't have an error message, it indicates a bug.
_LOG.info(
    f"child orders in child_order_df={child_order_df.shape[0]}\n\
responses in ccxt_order_response_df={ccxt_order_response_df.shape[0]}\n\
child_order_df-ccxt_order_response_df={child_order_df.shape[0]-ccxt_order_response_df.shape[0]}"
)

# %%
# Get child orders that were generated but did not get an order response.
no_response_orders = child_order_df[child_order_df["ccxt_id"] == -1]
no_response_orders["error_msg"] = no_response_orders["extra_params"].apply(
    lambda x: x.get("error_msg", "")
)

# %%
# Check the error messages for child orders that did not come through.
# Display error messages grouped by symbol.
# Get the universe to map asset_id's.
universe = ivcu.get_vendor_universe("CCXT", "trade", as_full_symbol=True)
asset_id_to_symbol_mapping = ivcu.build_numerical_to_string_id_mapping(universe)
no_response_orders["full_symbol"] = no_response_orders["asset_id"].map(
    asset_id_to_symbol_mapping
)
# Get value counts of error messages.
error_msg = no_response_orders.groupby("full_symbol")["error_msg"].value_counts()
error_msg

# %%
# Check child orders with no response and no logged error message.
no_response_orders[no_response_orders["error_msg"] == ""]

# %% [markdown]
# ## Consistency checks

# %% [markdown]
# - Number of trades per order and bar
# - Number of buys/sells
# - TWAP/VWAP difference

# %%
# For a given bar and a given instrument, we expect to see as number of trades between 0 and X,
# where X is the maximum number of child orders per bar.
bar_ccxt_order_aggregation[
    "order_count"
].stack().value_counts().sort_index().plot(kind="bar")

# %%
# Display order count by bar.
bar_ccxt_order_aggregation["order_count"]

# %%
# If `buy_count` and `sell_count` are both greater than zero for an order,
#  it indicates a bug.
has_buys = ccxt_order_fills["buy_count"] > 0
has_sells = ccxt_order_fills["sell_count"] > 0
# This dataframe should have zero rows.
ccxt_order_fills.loc[has_buys & has_sells].shape[0]

# %%
# If `buy_count` and `sell_count` are both greater than zero for an instrument and bar,
#  there is likely a bar alignment or timing problem.
has_buys = bar_fills["buy_count"] > 0
has_sells = bar_fills["sell_count"] > 0
# This dataframe should be empty.
bar_fills.loc[has_buys & has_sells].shape[0]

# %%
# If `order_twap` and `order_vwap` are different for a given instrument and bar,
#  then we are likely submitting orders of differing sizes.
# Use rounding to ignore machine precision artifacts.
#
# If this number is not zero, then closely inspect the dataframe (without summing
# absolute values).
bar_ccxt_order_aggregation["buy_limit_twap"].subtract(
    bar_ccxt_order_aggregation["buy_limit_vwap"]
).abs().sum().sum().round(9)

# %%
# Analogous check but for sells.
bar_ccxt_order_aggregation["sell_limit_twap"].subtract(
    bar_ccxt_order_aggregation["sell_limit_vwap"]
).abs().sum().sum().round(9)

# %%
# Plot order counts by timestamp.
col = "order_count"
coplotti.plot_boxplot(bar_ccxt_order_aggregation[col], "by_row", ylabel=col)

# %%
# Plot order counts by asset.
col = "order_count"
coplotti.plot_boxplot(bar_ccxt_order_aggregation[col], "by_col", ylabel=col)

# %% [markdown]
# # Fee summary

# %%
group_by_col = "is_buy"
obccexqu.generate_fee_summary(fills_df, "is_buy")

# %%
group_by_col = "is_maker"
obccexqu.generate_fee_summary(fills_df, group_by_col)

# %%
group_by_col = "is_positive_realized_pnl"
obccexqu.generate_fee_summary(fills_df, group_by_col)

# %%
group_by_col = "wave_id"
obccexqu.generate_fee_summary(fills_df, group_by_col)

# %%
# Get by-wave summary for a single test asset.
test_fills = fills_df[fills_df["asset_id"] == test_asset_id]
group_by_col = "wave_id"
obccexqu.generate_fee_summary(test_fills, group_by_col)

# %% [markdown]
# # Time to fill ECDFs

# %%
# Compute and plot time to fill ECDFs.
adj_fill_ecdfs = obccexqu.compute_adj_fill_ecdfs(
    fills_df, ccxt_order_response_df, child_order_df
)
adj_fill_ecdfs.plot()

# %%
# Plot ECDFs by child order wave.
if "wave_id" in child_order_df.columns:
    cplexsta.plot_adj_fill_ecdfs(
        fills_df,
        ccxt_order_response_df,
        child_order_df,
    )

# %% [markdown]
# # Execution quality

# %% [markdown]
# ## Compute `target_position_df` and `portfolio_df`

# %%
# TODO(Danya): add to config.
price_df = ohlcv_bars["close"]
target_position_df = oororcon.convert_order_df_to_target_position_df(
    parent_order_df,
    price_df,
)
hpandas.df_to_str(target_position_df, num_rows=5, log_level=logging.INFO)

# %%
portfolio_df = obccexqu.convert_bar_fills_to_portfolio_df(
    bar_fills,
    price_df,
)
hpandas.df_to_str(portfolio_df, num_rows=5, log_level=logging.INFO)

# %% [markdown]
# ## PNL and price for the test asset

# %%
portfolio_df["pnl"][test_asset_id].cumsum().plot()

# %%
target_position_df["price"][test_asset_id].plot()

# %% [markdown]
# ## Compute execution quality

# %%
(
    execution_quality_df,
    execution_quality_stats_df,
) = cftpdp.compute_execution_quality_df(
    portfolio_df,
    target_position_df,
)
hpandas.df_to_str(execution_quality_df, num_rows=5, log_level=logging.INFO)
hpandas.df_to_str(execution_quality_stats_df, num_rows=5, log_level=logging.INFO)

# %%
execution_quality_df.columns.levels[0].to_list()

# %%
coplotti.plot_execution_ecdfs(execution_quality_df)

# %% [markdown]
# ## Compute and plot portfolio stats

# %%
coplotti.plot_portfolio_stats(execution_quality_stats_df)

# %%
coplotti.plot_execution_stats(execution_quality_stats_df)

# %% [markdown]
# ##  Filled order execution quality

# %%
filled_order_execution_quality = obccexqu.compute_filled_order_execution_quality(
    filled_ccxt_orders, tick_decimals=6
)
filled_order_execution_quality.head()

# %%
# If any value is negative (up to machine precision), except for `direction`, it indicates a bug.
filled_order_execution_quality.loc[
    (filled_order_execution_quality.drop("direction", axis=1).round(9) < 0).any(
        axis=1
    )
].shape[0]

# %%
filled_order_execution_quality.min()

# %%
filled_order_execution_quality.max()

# %% [markdown]
# ## Compare bar trade prices to OHLCV TWAP

# %%
actual_and_ohlcv_price_df = {
    "buy_trade_price": trade_prices["buy_trade_price"],
    "sell_trade_price": trade_prices["sell_trade_price"],
    "twap": ohlcv_bars["twap"],
    "high": ohlcv_bars["high"],
    "low": ohlcv_bars["low"],
}
actual_and_ohlcv_price_df = pd.concat(actual_and_ohlcv_price_df, axis=1)
hpandas.df_to_str(actual_and_ohlcv_price_df, num_rows=5, log_level=logging.INFO)

# %%
actual_vs_ohlcv_execution_df = cofinanc.compute_ref_price_execution_quality(
    actual_and_ohlcv_price_df,
    "twap",
    "twap",
    "buy_trade_price",
    "sell_trade_price",
)
hpandas.df_to_str(
    actual_vs_ohlcv_execution_df, num_rows=5, log_level=logging.INFO
)

# %%
actual_vs_ohlcv_execution_df.columns.levels[0].to_list()

# %%
cofinanc.get_asset_slice(actual_and_ohlcv_price_df, test_asset_id).plot()

# %% [markdown]
# ## Spread and High-Low Range

# %% [markdown]
# ### Average bid/ask spread by asset_id

# %%
# Display average notional spread per instrument.
average_spread_notional = child_order_df.groupby("asset_id")["spread"].mean()
average_spread_notional

# %%
child_order_df.head()

# %%
# Display average spread per instrument in bps.
average_spread_bps = (
    child_order_df.groupby("asset_id")["spread_bps"].mean().sort_values()
)
average_spread_bps

# %%
average_spread_bps.plot(
    kind="bar",
    title="Average Spread per Instrument in Basis Points",
    xlabel="Instrument",
    ylabel="Average Spread (bps)",
)

# %% [markdown]
# ### OHLCV high-low spread to tick ratio

# %%
# Calculate the high-low spread to tick ratio.
high_low_range = (ohlcv_bars["high"] - ohlcv_bars["low"]) / price_tick_srs
# Plot average.
high_low_range.mean().sort_values(ascending=False).plot(
    kind="bar",
    logy=True,
    title="Average High/Low Range per Instrument (log)",
    xlabel="Instrument",
    ylabel="Average High/Low Range (log)",
)

# %% [markdown]
# # Child order DF stats

# %% [markdown]
# ## Volatility

# %%
# Average volatility in bps by asset.
# Note: `mean()` used as a placeholder.
child_order_df.groupby("asset_id")["total_vol_bps"].mean()

# %%
# Total volatility in bps histogram.
child_order_df["total_vol_bps"].hist()

# %% [markdown]
# ## Bid/ask

# %%
# Average bid cost by asset.
avg_bid_price_by_asset = child_order_df.groupby("asset_id")[
    "latest_bid_price"
].mean()
_LOG.info(avg_bid_price_by_asset)
avg_bid_size_by_asset = child_order_df.groupby("asset_id")[
    "latest_bid_size"
].mean()
_LOG.info(avg_bid_size_by_asset)
avg_bid_cost_by_asset = avg_bid_price_by_asset * avg_bid_size_by_asset
avg_bid_cost_by_asset

# %%
# Average ask cost by asset.
avg_ask_price_by_asset = child_order_df.groupby("asset_id")[
    "latest_ask_price"
].mean()
_LOG.info(avg_ask_price_by_asset)
avg_ask_size_by_asset = child_order_df.groupby("asset_id")[
    "latest_ask_size"
].mean()
_LOG.info(avg_ask_size_by_asset)
avg_ask_cost_by_asset = avg_ask_price_by_asset * avg_ask_size_by_asset
avg_ask_cost_by_asset

# %% [markdown]
# # Analyze filled orders

# %%
# Group child orders by corresponding parent order.
child_order_df["parent_order_id"] = child_order_df.extra_params.apply(
    lambda x: x["oms_parent_order_id"]
)
child_order_df_by_parent = child_order_df.reset_index().set_index(
    ["parent_order_id", "order_id"]
)

# %% [markdown]
# ## Filled order slippage normalized by parent order

# %% [markdown]
# Calculate filled order slippage as a difference between the bid/ask midpoint at the start of the parent order execution and the execution price of the filled order.

# %%
first_mid_price_by_parent = child_order_df_by_parent.groupby(level=0)[
    "latest_mid_price"
].first()

# %%
# Add filled order slippage for each child order.
child_order_df_by_parent["order_slippage"] = (
    first_mid_price_by_parent - child_order_df_by_parent["limit_price"]
)


# %%
# Filter to only unfilled child orders.
filled_child_order_df = child_order_df_by_parent[
    child_order_df_by_parent["ccxt_id"].isin(filled_ccxt_orders.index)
]
filled_child_order_df[["asset_id", "order_slippage"]]

# %% [markdown]
# # Analyze unfilled orders

# %% [markdown]
# ## Underfill execution quality

# %% [markdown]
# ### Underfill notional

# %%
# Get the total underfill notional for the run per asset.
execution_quality_df["underfill_notional"].abs().sum()

# %%
# Get the total underfill notional for the run per bar.
execution_quality_df["underfill_notional"].abs().sum(axis=1)

# %%
# Get the total underfill notional.
execution_quality_df["underfill_notional"].abs().sum().sum()

# %% [markdown]
# ### Aggregate fill rate

# %%
underfill_notional = execution_quality_df["underfill_notional"].abs().sum()
executed_volume_notional = portfolio_df["executed_trades_notional"].abs().sum()
executed_volume_notional / (underfill_notional + executed_volume_notional)

# %%
# Total portfolio aggregate fill rate.
total_underfill_notional = underfill_notional.sum()
total_executed_volume_notional = executed_volume_notional.sum()
total_executed_volume_notional / (
    total_underfill_notional + total_executed_volume_notional
)

# %% [markdown]
# ### Underfill share count

# %%
# Get underfill share count per asset.
execution_quality_df["underfill_share_count"].abs().sum().round(9)

# %% [markdown]
# ### Notional slippage

# %%
# Total slippage.
execution_quality_df["slippage_notional"].abs().sum().sum().round(9)

# %%
# Notional slippage by asset.
execution_quality_df["slippage_notional"].abs().sum().round(9)

# %%
# Slippage in bps by timestamp.
execution_quality_df["slippage_notional"].abs().sum(axis=1).round(9)

# %% [markdown]
# ### Slippage in bps adjusted by total executed volume

# %%
# Total.
execution_quality_df[
    "slippage_notional"
].abs().sum().sum() * 1e4 / executed_volume_notional.sum().round(9)

# %%
# By asset.
execution_quality_df[
    "slippage_notional"
].abs().sum() * 1e4 / executed_volume_notional.round(9)

# %%
# By timestamp.
execution_quality_df["slippage_notional"].abs().sum(axis=1) * 1e4 / portfolio_df[
    "executed_trades_notional"
].abs().sum(axis=1).round(9)


# %% [markdown]
# ### Slippage in bps over volatility

# %%
execution_quality_df["slippage_in_bps"].mean() / average_spread_bps

# %% [markdown]
# ## Compute vol-adjusted close price

# %%
# Get `close` price as defined in `get_adjusted_close_price` docstring.
child_order_df_by_parent["close"] = child_order_df_by_parent.groupby(level=0)[
    "latest_mid_price"
].shift(-1)
# Calculate volatility-adjusted close price.
adjusted_close = list(
    map(
        obccexqu.get_adjusted_close_price,
        child_order_df_by_parent["close"],
        child_order_df_by_parent["latest_mid_price"],
        child_order_df_by_parent["total_vol"],
    )
)
child_order_df_by_parent["adj_close"] = adjusted_close

# %%
# Filter to only unfilled child orders.
unfilled_child_order_df = child_order_df_by_parent[
    child_order_df_by_parent["ccxt_id"].isin(unfilled_ccxt_orders.index)
]
unfilled_child_order_df.head(3)

# %%
# Separate unfilled child orders by side.
unfilled_child_order_df_buy = unfilled_child_order_df[
    unfilled_child_order_df["diff_num_shares"] > 0
]
unfilled_child_order_df_sell = unfilled_child_order_df[
    unfilled_child_order_df["diff_num_shares"] < 0
]

# %%
# Display mean adjusted close by side.
_LOG.info(
    "Mean adjusted close for unfilled buy orders: %s",
    unfilled_child_order_df_buy["adj_close"].dropna().mean(),
)
_LOG.info(
    "Mean adjusted close for unfilled sell orders: %s",
    unfilled_child_order_df_sell["adj_close"].dropna().mean(),
)

# %%
# Display mean adjusted close by wave_id, buy orders.
unfilled_child_order_df_buy.groupby("wave_id")["adj_close"].mean().round(9)

# %%
# Display mean adjusted close by wave_id, sell orders.
unfilled_child_order_df_sell.groupby("wave_id")["adj_close"].mean().round(9)

# %% [markdown]
# # Config after notebook run

# %%
print(config.to_string(mode="verbose"))

# %%
