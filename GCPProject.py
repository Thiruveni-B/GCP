from airflow import DAG
from airflow.operators.dummy import DummyOperator
from airflow.contrib.operators.gcs_to_bq import GoogleCloudStorageToBigQueryOperator
from airflow.operators.bash_operator import BashOperator
from airflow.utils.dates import datetime, timedelta
from google.cloud import storage

# Define GCS bucket name
bucket_name = 'composer-input-data'
yesterday = datetime.combine(datetime.today() - timedelta(1), datetime.min.time()) #start time

# Function to retrieve list of files from GCS bucket
def list_files_in_bucket(bucket_name):
    try:
        storage_client = storage.Client()  #initialize the google cloud storage client
        bucket = storage_client.bucket(bucket_name)   #retrives the specified bucket by its name
        blobs = bucket.list_blobs()   #retrives a list of all blobs in gcs
        
        files = [blob.name for blob in blobs if blob.name.startswith(('log', 'ref')) and blob.name.endswith('.csv')]    # filtering the files which starts with log or ref
        return files
    except Exception as e:   
        # Log error and raise exception
        print(f"Error listing files in bucket: {e}")1
        raise

# Define DAG and creates a dag object
dag = DAG(
    dag_id='GCS_TO_BIGQUERY_WITH_CLEANUP',  # its like dag name 
    catchup=False, # executes the current interval .won't execute the any missed files to prevent unneccesary processing
    #if it is true it will process the old data for historical data
    schedule_interval='@daily', # specifies the frequently that dag to run
    start_date=yesterday,  # start date from which dag should  start running
    default_args={
        'email_on_failure': False, # TRUE -if the dag is failed then airflow send a mai.....for false it won't send any mail
        'email_on_retry': False,  # True- email send for each retries if false means it won't send mail but it will retry
        'retries': 2,
        'retry_delay': timedelta(minutes=5)   # time delay between each retry
    }
)

#__________________TASK__________________________


# Part 1: Tasks Creation (Name: task_creation)
start = DummyOperator(task_id='start', dag=dag)
end = DummyOperator(task_id='end', dag=dag)

try:
    files_in_bucket = list_files_in_bucket(bucket_name)

    if files_in_bucket:

        # Dictionary to store tasks related to each file
        file_tasks = {}

        for file_name in files_in_bucket:
            if file_name.startswith('log'):
                table_name = 'log_table'
            elif file_name.startswith('ref'):
                table_name = 'reference_table'
            else:
                continue
            
            task_id = f'load_{file_name.replace(".csv", "")}_to_{table_name}'
            table_id = f'dawn-light-0502.gcs_to_bigquery.{table_name}'

            # Create a dictionary to store the task for current file
            file_tasks[file_name] = {}   # to store task related info for each file(based on key) that was found in gcs .helps to manage the load proccng 

            # creating the file_found_task and storing it in the dict
            file_tasks[file_name]['file_found'] = DummyOperator(   
                task_id=f'file_found_{file_name}',  # dummy op as placeholder temporarly holds the file 
                dag=dag)
            
            # definfing task dependency
            start >> file_tasks[file_name]['file_found']

            # creating the load_task and storing it in the dict (file name as key)
            file_tasks[file_name]['load_file'] = GoogleCloudStorageToBigQueryOperator(
                task_id=task_id,
                bucket=bucket_name,
                source_objects=[file_name],
                destination_project_dataset_table=table_id,
                autodetect=True,   # automatically detects the schema
                skip_leading_rows=1,  # skip the first row
                create_disposition='CREATE_IF_NEEDED',  # if desired destination table is not there it will create new table
                write_disposition='WRITE_APPEND', #handles the existing table when new table is loaded (new data is appended to existing table)
                dag=dag
            )

            # creating the remove_file_task and storing it in the dict
            file_tasks[file_name]['remove_file'] = BashOperator(
                task_id=f'remove_file_{file_name}',
                bash_command=f'gsutil rm gs://{bucket_name}/{file_name}',
                dag=dag
            )

            # Establish task dependencies
            file_tasks[file_name]['file_found'] >> file_tasks[file_name]['load_file'] >> file_tasks[file_name]['remove_file'] >> end

    else:
        no_files_task = DummyOperator(task_id='no_files', dag=dag)
        start >> no_files_task >> end

except Exception as e:
    # Log error and fail the DAG
    print(f"Error in DAG execution: {e}")
    raise

# Log DAG status
log_status = BashOperator(
    task_id='log_status',
    bash_command='echo DAG execution completed',
    dag=dag
)

# Add logging statements within the DAG tasks
start.log.info('Starting DAG execution')  # indicating the start of dag execution

for file_name in files_in_bucket:
    file_tasks[file_name]['file_found'].log.info(f'File found: {file_name}')
    file_tasks[file_name]['load_file'].log.info(f'Loading {file_name} to BigQuery')
    file_tasks[file_name]['remove_file'].log.info(f'Remove task created for file: {file_name}')

end.log.info('DAG execution completed') #completion of log
