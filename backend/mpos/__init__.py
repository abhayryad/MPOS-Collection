"""MPOS Collection - POS collection slips and reports over datav2 (server 28)."""
import warnings

# snowflake-connector warns about the installed pyarrow version; pyarrow is only used by its
# pandas helpers, which this app does not use.
warnings.filterwarnings("ignore", message=".*incompatible version of 'pyarrow'")
