import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_squared_error
import pickle

class StockPricePredictor:
    def __init__(self):
        self.model = LinearRegression()

    def train(self, combined_data):
        X = combined_data[["temperature", "min_temperature", "max_temperature", "humidity", "wind_speed"]]
        y = combined_data["price"]
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        self.model.fit(X_train, y_train)
        y_pred = self.model.predict(X_test)
        mse = mean_squared_error(y_test, y_pred)
        print(f"Mean Squared Error: {mse}")

    def predict(self, weather_data):
        X_new = weather_data[["temperature", "min_temperature", "max_temperature", "humidity", "wind_speed"]]
        predictions = self.model.predict(X_new)
        return predictions

    def save_model(self, file_path):
        with open(file_path, 'wb') as f:
            pickle.dump(self.model, f)

    def load_model(self, file_path):
        with open(file_path, 'rb') as f:
            self.model = pickle.load(f)