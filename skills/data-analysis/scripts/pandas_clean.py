"""
Safe snippet for basic pandas cleaning. Copy and adapt for your dataset.
Run: python pandas_clean.py [path/to/data.csv]  (ensure pandas is installed)

Exits with code 1 and a message on stderr when the input cannot be loaded or is empty.
"""
import sys

try:
    import pandas as pd
except ModuleNotFoundError:
    sys.exit("pandas is not installed. Install it with: pip install pandas")


def load_csv(path: str) -> pd.DataFrame:
    """Load `path`, exiting with a readable message and code 1 on any input problem."""
    try:
        df = pd.read_csv(path)  # or read_json, read_excel
    except FileNotFoundError:
        sys.exit(f"Input file not found: {path}")
    except PermissionError:
        sys.exit(f"Permission denied while reading: {path}")
    except UnicodeDecodeError:
        sys.exit(f"{path} is not valid UTF-8 text. Re-export it as UTF-8 or pass an explicit encoding.")
    except pd.errors.EmptyDataError:
        sys.exit(f"{path} is empty: there is nothing to clean.")
    except pd.errors.ParserError as error:
        sys.exit(f"{path} is not a well-formed CSV file: {error}")
    if df.empty:
        sys.exit(f"{path} has a header but no data rows.")
    return df


def main(path: str) -> None:
    """Run the cleaning steps and print a short summary of each one."""
    df = load_csv(path)
    print("df_shape", df.shape)
    print("df_dtypes", df.dtypes)

    # Drop fully null columns
    df = df.dropna(axis=1, how="all")
    print("df_shape_after_drop_all_null_cols", df.shape)

    # Fill or drop nulls in key columns (customise columns)
    # df = df.dropna(subset=["required_col"])
    # df["optional_col"] = df["optional_col"].fillna(0)

    # Normalise column names (optional)
    df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")
    print("df_columns", list(df.columns))

    # Deduplicate (optional)
    before = len(df)
    df = df.drop_duplicates()
    print("rows_dropped_duplicates", before - len(df))

    # Sample output
    print("df_head", df.head())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data.csv")
