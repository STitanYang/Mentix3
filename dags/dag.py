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
sys.path.append('/opt/airflow/dags/programs/')
from programs.model import StockPricePredictor
import json
import numpy as np

load_dotenv()
predictor = StockPricePredictor()

import pandas as pd
from datetime import datetime, timedelta
from airflow.providers.postgres.hooks.postgres import PostgresHook

def fetch_data(ti):
    # Initialize PostgresHook
    postgres_hook = PostgresHook(postgres_conn_id='qwer')
    
    # Fetch Weather Data
    weather_query = "SELECT * FROM public.\"Weather\";"
    weather_conn = postgres_hook.get_conn()
    weather_cursor = weather_conn.cursor()
    weather_cursor.execute(weather_query)
    weather_columns = [desc[0] for desc in weather_cursor.description]
    weather_rows = weather_cursor.fetchall()
    weather_cursor.close()
    weather_df = pd.DataFrame(weather_rows, columns=weather_columns)
    
    # Transform Weather Data
    base_date = datetime(2024, 1, 1)
    weather_df["date"] = weather_df["doy"].apply(
        lambda doy: (base_date + timedelta(days=doy - 1)).strftime('%Y-%m-%d')
    )
    weather_df.rename(
        columns={
            "t2m": "temperature",
            "t2m_min": "min_temperature",
            "t2m_max": "max_temperature",
            "rh2m": "humidity",
            "ws2m": "wind_speed",
        },
        inplace=True,
    )
    weather_df = weather_df[["date", "temperature", "min_temperature", "max_temperature", "humidity", "wind_speed"]]

    # Fetch Stock Data
    stock_query = "SELECT * FROM public.\"Stock\";"
    stock_conn = postgres_hook.get_conn()
    stock_cursor = stock_conn.cursor()
    stock_cursor.execute(stock_query)
    stock_columns = [desc[0] for desc in stock_cursor.description]
    stock_rows = stock_cursor.fetchall()
    stock_cursor.close()
    stock_df = pd.DataFrame(stock_rows, columns=stock_columns)

    # Transform Stock Data
    weather_df["date"] = pd.to_datetime(weather_df["date"])
    weather_df = weather_df[weather_df["date"].dt.weekday < 5]  # Filter weekdays
    stock_df["Date"] = pd.to_datetime(stock_df["Date"], format="%m/%d/%Y")

    # Combine Data
    combined_data = pd.merge(
        weather_df,
        stock_df,
        left_on="date",
        right_on="Date",
        how="inner"
    )
    combined_data.drop(columns=["Date"], inplace=True)
    combined_data.rename(columns={"close": "price"}, inplace=True)

    # Push Combined Data to XCom
    ti.xcom_push(key='data', value=combined_data.to_json())  # Convert DataFrame to JSON for XCom

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
        "date": datetime.today().strftime("%Y-%m-%d"),
        "temperature": data["main"]["temp"],
        "min_temperature": data["main"]["temp_min"],
        "max_temperature": data["main"]["temp_max"],
        "humidity": data["main"]["humidity"],
        "wind_speed": data["wind"]["speed"],
    }
    print(weather_info)
    ti.xcom_push(key='weather_data', value = weather_info)

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
    ti.xcom_push(key='weather_forecast', value=weather_df.to_json())

def get_stock_data(ti):
    # Polygon.io API URL for daily stock data
    stock_api_url = "https://api.polygon.io/v2/aggs/ticker/{symbol}/prev"

    # Replace 'AAPL' with the stock symbol you want, or make it dynamic
    stock_symbol = "AAPL"
    
    # Your Polygon.io API key
    api_key = "AhNf6SXPcKVLpqRKXwkx8PNhiHvo3Zwp"
    
    # Construct the request URL
    url = stock_api_url.format(symbol=stock_symbol)
    
    # Define the query parameters
    params = {
        "apiKey": api_key
    }
    
    # Send the GET request to the API
    response = requests.get(url, params=params)
    
    # Check if the response is successful (status code 200)
    if response.status_code == 200:
        data = response.json()
        
        # Extract the date and price from the response
        if 'results' in data and len(data['results']) > 0:
            stock_data = data['results'][0]  # Get the first result (latest data)
            
            # Extract the date and price of the stock
            last_refreshed = datetime.fromtimestamp(stock_data['t'] / 1000).strftime('%Y-%m-%d')
            stock_price = stock_data['c']  # Close price (or open price if needed)

            # Construct the stock_info dictionary
            stock_info = {
                "date": last_refreshed,
                "price": float(stock_price)
            }
            
            # Push the data to XCom for use in the next task
            ti.xcom_push(key='stock_data', value=stock_info)
            print("Stock Data Structure:")
            print(len(stock_info))  # Number of top-level keys in the stock dictionary
            print(stock_info.keys())  # List of top-level keys
            print(stock_info)  # Print the whole structure if it's not too large

        else:
            raise ValueError("No data found for the stock symbol")
    else:
        # Handle API request error
        raise Exception(f"Error fetching stock data: {response.status_code}")

def get_postgres_data(ti):
    # Create a PostgresHook instance using the connection ID
    postgres_hook = PostgresHook(postgres_conn_id="asdf")
    
    # SQL query to fetch data
    sql_query = """
    SELECT * FROM weatherstocks;
    """
    
    # Execute the query and fetch results
    connection = postgres_hook.get_conn()
    cursor = connection.cursor()
    cursor.execute(sql_query)
    
    # Fetch column names and data rows
    columns = [desc[0] for desc in cursor.description]  # Get column names
    rows = cursor.fetchall()  # Fetch all rows

    # Convert the result into a Pandas DataFrame
    df = pd.DataFrame(rows, columns=columns)

    # Push the DataFrame to XCom as JSON
    ti.xcom_push(key='postgres_data', value=df.to_json())

def add_data(ti):
    # Pull the data from XCom
    data_json = ti.xcom_pull(key='data', task_ids='fetch_data')
    postgres_data_json = ti.xcom_pull(key='postgres_data', task_ids='get_postgres_data')
    
    # Convert JSON to DataFrame
    data = pd.read_json(data_json)
    postgres_data = pd.read_json(postgres_data_json)
    
    # Now concatenate the DataFrames
    combined_df = pd.concat([data, postgres_data], ignore_index=True)
    
    # Push the combined DataFrame back to XCom
    ti.xcom_push(key='full_data', value=combined_df)

def combine_data(ti):
    # Pull the weather and stock data from XCom
    weather = ti.xcom_pull(key='weather_data', task_ids='get_weather_data')
    stock = ti.xcom_pull(key='stock_data', task_ids='get_stock_data')


    Stock = pd.DataFrame([stock])
    Weather = pd.DataFrame([weather])
    
    # Ensure both 'date' columns are datetime64[ns] for merging
    Weather["date"] = pd.to_datetime(Weather["date"], errors='coerce')  # Coerce to handle any invalid date format
    Stock["date"] = pd.to_datetime(Stock["date"], errors='coerce')
    print(Stock.head())
    print(Weather.head())
    value = Stock.loc[0, 'price']
    value_array = np.array([value])
    print(value_array)

    # Merge the dataframes on the 'date' column
    Weather["price"] = value_array
    print(Weather.head())
    # Push the combined data to XCom
    ti.xcom_push(key='combined_data', value=Weather)  # Save as JSON (you can adjust the format)



def train_model(ti):
    full_data = ti.xcom_pull(key='full_data', task_ids='add_data')
    model_directory = '/tmp/programs'
    if not os.path.exists(model_directory):
        os.makedirs(model_directory)

    model_path = os.path.join(model_directory, 'model.pkl')
    predictor.train(full_data)
    predictor.save_model(model_path)

from sklearn.impute import SimpleImputer

def predict_prices(ti):
    weather_forecast = ti.xcom_pull(key='weather_forecast', task_ids='get_weather_forecast')
    
    # If it's a string, parse it into a dictionary
    if isinstance(weather_forecast, str):
        weather_forecast = json.loads(weather_forecast)
    
    print(f"Weather forecast data: {weather_forecast}")
    
    # Ensure that weather_forecast is not empty
    if not weather_forecast:
        raise ValueError("Weather forecast data is empty")

    # Convert to DataFrame and ensure columns are named properly
    df = pd.DataFrame(weather_forecast)  # If weather_forecast is a dict, it will be a row

    # Check if there are missing values after conversion
    if df.isnull().values.any():
        print("DataFrame contains missing values. Applying imputation.")
        imputer = SimpleImputer(strategy='mean')
        df = pd.DataFrame(imputer.fit_transform(df), columns=df.columns)
    
    model_directory = '/tmp/programs/model.pkl'
    predictor.load_model(model_directory)
    print(df)
    
    # Make predictions
    predictions = predictor.predict(df)

    df["price"] = predictions
    print(df.head())

    # Push the predictions as a list
    ti.xcom_push(key='predictions', value=df)

def load_data_postgres(ti):
    combined = ti.xcom_pull(key='combined_data', task_ids='combine_data')
    df = pd.DataFrame(combined)
    postgres_hook = PostgresHook(postgres_conn_id='asdf')
    insert_query = """
    INSERT INTO weatherstocks (date, temperature, min_temperature, max_temperature, humidity, wind_speed, price)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    
    # Iterate through the DataFrame rows
    for _, row in df.iterrows():
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
    predictions_data = ti.xcom_pull(key='predictions', task_ids='predict_prices')
    print(predictions_data)
    df = pd.DataFrame(predictions_data)
    postgres_hook = PostgresHook(postgres_conn_id='asdfg')
    insert_query = """
    INSERT INTO predictions (date, temperature, min_temperature, max_temperature, humidity, wind_speed, price)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    
    # Iterate through the DataFrame rows
    for _, row in df.iterrows():
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
        id INT,
        date TIMESTAMP,
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
    postgres_conn_id='asdfg',
    sql="""
    CREATE TABLE IF NOT EXISTS predictions (
        id INT,
        date TIMESTAMP,
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

load_data_postgres_task = PythonOperator(
    task_id='load_data_postgres',
    python_callable=load_data_postgres,
    dag=dag,
)

delete_prediction_table_task = PostgresOperator(
    task_id='delete_prediction_table',
    postgres_conn_id='asdfg',
    sql="""
    DELETE FROM predictions;
    """,
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
create_data_table_task >> load_data_postgres_task
[create_prediction_table_task, predict_prices_task, delete_prediction_table_task] >> load_prediction_postgres_task