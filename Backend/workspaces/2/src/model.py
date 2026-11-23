import pandas as pd
import numpy as np
import yfinance as yf
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

def fetch_stock_data(ticker: str, period: str = '2y') -> pd.DataFrame:
    """Fetch historical stock data using yfinance."""
    stock = yf.Ticker(ticker)
    df = stock.history(period=period)
    if df.empty:
        raise ValueError(f"No data found for ticker {ticker}")
    return df

def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Create technical features and target for ML prediction."""
    df = df.copy()
    df['Return'] = df['Close'].pct_change()
    df['Target'] = (df['Close'].shift(-1) > df['Close']).astype(int)
    df['MA5'] = df['Close'].rolling(window=5).mean()
    df['MA20'] = df['Close'].rolling(window=20).mean()
    df['Volatility'] = df['Return'].rolling(window=5).std()
    df = df.dropna()
    return df

def train_model(df: pd.DataFrame):
    """Train a Random Forest classifier for next-day price direction."""
    features = ['MA5', 'MA20', 'Volatility', 'Volume']
    X = df[features]
    y = df['Target']
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
    model = RandomForestClassifier(n_estimators=100, random_state=42)
    model.fit(X_train, y_train)
    
    score = model.score(X_test, y_test)
    return model, score
