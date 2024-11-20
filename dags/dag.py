from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta
import requests
import pandas as pd
from dotenv import load_dotenv
import os
from airflow.providers.postgres.operators.postgres import PostgresOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
import sys
sys.path.append('../dags/programs/')
from programs.model import StockPricePredictor

MODEL_PATH = "./programs/model.pkl"

load_dotenv()
predictor = StockPricePredictor()

def fetch_data(ti):
    file_path_csv = './data/weather_dat2.csv'

    try:
        data = pd.read_csv(file_path_csv, sep=';')
    except Exception as e:
        content_preview_csv = str(e)

    base_date = datetime(2024, 1, 1)
    data["date"] = data["DOY"].apply(lambda doy: (base_date + datetime.timedelta(days=doy - 1)).strftime('%Y-%m-%d'))
    data.rename(columns={
        "T2M": "temperature",
        "T2M_MIN": "min_temperature",
        "T2M_MAX": "max_temperature",
        "RH2M": "humidity",
        "WS2M": "wind_speed",
    }, inplace=True)

    data = data[["date", "temperature", "min_temperature", "max_temperature", "humidity", "wind_speed"]]

    file_path_csv = './data/stock_data.csv'

    try:
        df = pd.read_csv(file_path_csv, sep=';')
    except Exception as e:
        content_preview_csv = str(e)

    data["date"] = pd.to_datetime(data["date"])
    data = data[data["date"].dt.weekday < 5]
    df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y")

    combined_data = pd.merge(data, df, left_on="date", right_on="Date", how="inner")
    combined_data = combined_data.drop(columns=["Date"])
    combined_data["price"] = combined_data["Close"]
    combined_data = combined_data.drop(columns=["Close"])
    ti.xcom_push(key='data', value=combined_data)

def get_weather_data(ti):
    weather_api_url = "https://api.openweathermap.org/data/2.5/weather"
    weather_params = {
        "q": "San Francisco",
        "appid": "de9efc526d3e1e60bef8073c1075667c",
        "units": "metric"
    }
    response = requests.get(weather_api_url, params=weather_params)
    data = response.json()

    weather_info = {
        "date": datetime(2024, 11, 18).strftime("%Y-%m-%d"),  # Tanggal tetap
        "temperature": data["main"]["temp"],
        "min_temperature": data["main"]["temp_min"],
        "max_temperature": data["main"]["temp_max"],
        "humidity": data["main"]["humidity"],
        "wind_speed": data["wind"]["speed"],
    }
    ti.xcom_push(key='weather_data', value=pd.DataFrame([weather_info]))

def get_weather_forecast(ti):
    # OpenWeather API endpoint
    weather_api_url = "http://api.openweathermap.org/data/2.5/forecast/daily"
    
    # API parameters
    weather_params = {
        "lat": 37.7749,  # Latitude of the location (e.g., San Francisco)
        "lon": -122.4194,  # Longitude of the location
        "cnt": 16,  # Number of days to forecast
        "appid": "bd5e378503939ddaee76f12ad7a97608",  # Replace with your API key
        "units": "metric"  # Get data in Celsius
    }
    
    # API call
    response = requests.get(weather_api_url, params=weather_params)
    data = response.json()

    # Debugging: Print the API response to check its structure
    print(data)

    # Check if the expected data is present
    if "list" not in data:
        raise KeyError(f"Unexpected API response structure: {data.get('message', 'No error message provided')}")

    # Parse the response into a DataFrame
    forecast_data = []
    for day in data["list"]:
        forecast_data.append({
            "date": datetime.utcfromtimestamp(day["dt"]).strftime("%Y-%m-%d"),
            "temperature": day["temp"]["day"],
            "min_temperature": day["temp"]["min"],
            "max_temperature": day["temp"]["max"],
            "humidity": day["humidity"],
            "wind_speed": day.get("speed"),
        })
    
    weather_df = pd.DataFrame(forecast_data)
    weather_df.to_csv('/tmp/weather_forecast_data.csv', index=False)  # Save data temporarily
    print("7-day weather forecast data extracted successfully.")
    ti.xcom_push(key='weather_forecast', value=weather_df)
    return weather_df

def get_stock_data(ti):
    stock_api_url = "https://www.alphavantage.co/query"
    stock_params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": "AAPL",
        "apikey": os.getenv("API_KEY_2"),
    }
    response = requests.get(stock_api_url, params=stock_params)
    data = response.json()
    
    last_refreshed = data["Meta Data"]["3. Last Refreshed"]
    stock_price = data["Time Series (Daily)"][last_refreshed]["1. open"]
    
    stock_info = {
        "date": last_refreshed.split(" ")[0],
        "price": float(stock_price)
    }
    ti.xcom_push(key='stock_data', value=pd.DataFrame([stock_info]))

def get_postgres_data(ti):
    # Create a PostgresHook instance using the connection ID
    postgres_hook = PostgresHook(postgres_conn_id="asdf")
    
    # SQL query to fetch data
    sql_query = """
    SELECT * FROM WeatherStock;
    """
    
    # Execute the query and fetch results
    connection = postgres_hook.get_conn()
    cursor = connection.cursor()
    cursor.execute(sql_query)
    
    # Convert the result into a Pandas DataFrame
    columns = [desc[0] for desc in cursor.description]  # Get column names
    data = cursor.fetchall()  # Fetch all rows
    df = pd.DataFrame(data, columns=columns)
    ti.xcom_push(key='postgres_data', value = df)

def add_data(ti):
    data = ti.xcom_pull(key='data', task_ids='fetch_data')
    postgres_data = ti.xcom_pull(key='postgres_data', task_ids='get_postgres_data')
    combined_df = pd.concat([data, postgres_data], ignore_index=True)
    ti.xcom_push(key='full_data', value = combined_df)

def combine_data(ti):
    weather = ti.xcom_pull(key='weather_data', task_ids='get_weather_data')
    stock = ti.xcom_pull(key='stock_data', task_ids='get_stock_data')
    stock["date"] = pd.to_datetime(stock["date"])
    combined_data = pd.merge(weather, stock, left_on="date", right_on="date", how="inner")
    ti.xcom_push(key='combined_data', value = combined_data)

def train_model(ti):
    full_data = ti.xcom_pull(key='full_data', task_ids='add_data')
    predictor.train(full_data)
    predictor.save_model(MODEL_PATH)

def predict_prices(ti):
    weather_forecast = ti.xcom_pull(key='weather_forecast', task_ids='get_weather_forecast')
    predictor.load_model(MODEL_PATH)
    predictions = predictor.predict(weather_forecast)
    ti.xcom_push(key='predictions', value = predictions)

def combine_predictions(ti):
    weather_forecast = ti.xcom_pull(key='weather_forecast', task_ids='get_weather_forecast')
    predictions = ti.xcom_pull(key='predictions', task_ids='predict_prices')
    weather_forecast["price"] = predictions
    ti.xcom_push(key='predictions_data', value = weather_forecast)

def load_data_postgres(ti):
    combined_data = ti.xcom_pull(key='combined_data', task_ids='combine_data')
    postgres_hook = PostgresHook(postgres_conn_id='asdf')
    insert_query = """
    INSERT INTO WeatherStock (date, temperature, min_temperature, max_temperature, humidity, wind_speed, price)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    
    # Iterate through the DataFrame rows
    for _, row in combined_data.iterrows():
        # Extract values from the DataFrame row and insert them into the database
        postgres_hook.run(
            insert_query,
            parameters=(
                row['date'],
                row['temperature'],
                row['min_temperature'],
                row['max_temperature'],
                row['humidity'],
                row['wind_speed'],
                row['price'],
            )
        )

def load_prediction_postgres(ti):
    predictions_data = ti.xcom_pull(key='predictions_data', task_ids='combine_predictions')
    postgres_hook = PostgresHook(postgres_conn_id='asdfg')
    insert_query = """
    INSERT INTO predictions (date, temperature, min_temperature, max_temperature, humidity, wind_speed, price)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    
    # Iterate through the DataFrame rows
    for _, row in predictions_data.iterrows():
        # Extract values from the DataFrame row and insert them into the database
        postgres_hook.run(
            insert_query,
            parameters=(
                row['date'],
                row['temperature'],
                row['min_temperature'],
                row['max_temperature'],
                row['humidity'],
                row['wind_speed'],
                row['price'],
            )
        )

# Default arguments untuk DAG Airflow
default_args = {
    'owner': 'airflow',
    'retries': 1,
    'retry_delay': timedelta(minutes=5)
}

dag = DAG(
    'weather_stock_prediction',
    default_args=default_args,
    description='A DAG to fetch weather and stock data, train model and make predictions',
    schedule_interval=timedelta(days=1),  # Adjust the schedule as needed (daily in this case)
    start_date=datetime(2024, 11, 20),   # Adjust the start date as needed
    catchup=False,  # Whether to backfill past runs
)

# Define the tasks in the DAG
fetch_data_task = PythonOperator(
    task_id='fetch_data',
    python_callable=fetch_data,
    dag=dag,
)

get_weather_data_task = PythonOperator(
    task_id='get_weather_data',
    python_callable=get_weather_data,
    dag=dag,
)

get_weather_forecast_task = PythonOperator(
    task_id='get_weather_forecast',
    python_callable=get_weather_forecast,
    dag=dag,
)

get_stock_data_task = PythonOperator(
    task_id='get_stock_data',
    python_callable=get_stock_data,
    dag=dag,
)

create_data_table_task = PostgresOperator(
    task_id='create_data_table',
    postgres_conn_id='asdf',
    sql="""
    CREATE TABLE IF NOT EXISTS WeatherStocks (
        date DATETIME
        temperature FLOAT, 
        min_temperature FLOAT,
        max_temperature FLOAT,
        humidity FLOAT,
        wind_speed FLOAT,
        price FLOAT
    );
    """,
    dag=dag,
)

create_prediction_table_task = PostgresOperator(
    task_id='create_prediction_table',
    postgres_conn_id='asdf',
    sql="""
    CREATE TABLE IF NOT EXISTS predictions (
        date DATETIME
        temperature FLOAT, 
        min_temperature FLOAT,
        max_temperature FLOAT,
        humidity FLOAT,
        wind_speed FLOAT,
        price FLOAT
    );
    """,
    dag=dag,
)

get_postgres_data_task = PythonOperator(
    task_id='get_postgres_data',
    python_callable=get_postgres_data,
    dag=dag,
)

combine_data_task = PythonOperator(
    task_id='combine_data',
    python_callable=combine_data,
    dag=dag,
)

add_data_task = PythonOperator(
    task_id='add_data',
    python_callable=add_data,
    dag=dag,
)

train_model_task = PythonOperator(
    task_id='train_model',
    python_callable=train_model,
    dag=dag,
)

predict_prices_task = PythonOperator(
    task_id='predict_prices',
    python_callable=predict_prices,
    dag=dag,
)

combine_predictions_task = PythonOperator(
    task_id='combine_predictions',
    python_callable=combine_predictions,
    dag=dag,
)

load_data_postgres_task = PythonOperator(
    task_id='load_data_postgres',
    python_callable=load_data_postgres,
    dag=dag,
)

load_prediction_postgres_task = PythonOperator(
    task_id='load_prediction_postgres',
    python_callable=load_prediction_postgres,
    dag=dag,
)

# Set task dependencies
[get_weather_data_task, get_stock_data_task] >> combine_data_task
[combine_data_task, fetch_data_task] >> create_data_table_task
[create_data_table_task, fetch_data_task] >> add_data_task
add_data_task >> train_model_task
[train_model_task, get_weather_forecast_task] >> predict_prices_task
[predict_prices_task, get_weather_forecast_task] >> combine_predictions_task
combine_data_task >> load_data_postgres_task
[create_prediction_table_task, combine_predictions_task] >> load_prediction_postgres_task