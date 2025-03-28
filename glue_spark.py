import sys
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql.functions import explode, col, to_date,regexp_replace,collect_list,concat_ws
from datetime import datetime
from awsglue.dynamicframe import DynamicFrame
import boto3

## @params: [JOB_NAME]
args = getResolvedOptions(sys.argv, ['JOB_NAME'])

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)
job.commit()

channel_data_path ="s3://airline-data-ingestion-temp/raw_data/to_processed/channel_data/"
videos_data_path = "s3://airline-data-ingestion-temp/raw_data/to_processed/video_data/"
comments_data_path ="s3://airline-data-ingestion-temp/raw_data/to_processed/comments_data/"

channel_df = spark.read.json(channel_data_path)
videos_df = spark.read.json(videos_data_path)
comments_df = spark.read.json(comments_data_path)


def process_channel_data(channel_df):
    channel_data = channel_df.withColumn("items",explode("items")).select(
        col("items.kind").alias("kind"),
        col("items.id").alias("channel_id"),
        col("items.contentDetails.relatedPlaylists.uploads").alias("playlist_id"),
        col("items.snippet.title").alias("channel_title"),
        regexp_replace(regexp_replace(col("items.snippet.description"), "," , ";"), "\s+", " ").alias("channel_description"), 
        col("items.snippet.publishedAt").alias("channel_publishedAt"),
        col("items.snippet.country").alias("channel_country"),
        col("items.statistics.viewCount").alias("channel_viewCount"),
        col("items.statistics.subscriberCount").alias("channel_subscriberCount"),
        col("items.statistics.videoCount").alias("channel_videoCount")
    ).drop_duplicates(["channel_id"])

    channel_data = channel_data.withColumn("channel_publishedAt", to_date(col("channel_publishedAt")))

    int_columns =['channel_viewCount', 'channel_subscriberCount', 'channel_videoCount']

    for column in int_columns:
        channel_data = channel_data.withColumn(column, col(column).cast("int"))

    return channel_data

def process_videos_data(videos_df):
    videos_data = videos_df.withColumn("items",explode("items")).select(
        col("items.snippet.channelId").alias("channelId"),
        col("items.id").alias("video_id"),
        col("items.snippet.publishedAt").alias("publishedAt"),       
        regexp_replace(regexp_replace(col("items.snippet.title"), ",", ";"), "\s+", " ").alias("title"),       
        regexp_replace(regexp_replace(col("items.snippet.description"), ",", ";"), "\s+", " ").alias("description"),      
        col("items.snippet.categoryId").alias("categoryId"),
        col("items.statistics.viewCount").alias("viewCount"),
        col("items.statistics.likeCount").alias("likeCount"),
        col("items.statistics.commentCount").alias("commentCount")
    ).drop_duplicates(["video_id"])

    videos_data = videos_data.withColumn("publishedAt",to_date(col("publishedAt")))

    int_columns =['categoryId', 'viewCount', 'likeCount','commentCount']

    for column in int_columns:
        videos_data = videos_data.withColumn(column, col(column).cast("int"))


    return videos_data


def process_comments_data(comments_df):
    Comments_Data = comments_df.withColumn("items",explode('items')).select(
        col("items.snippet.channelId").alias("channelId"),
        col("items.snippet.videoId").alias("videoId"),
        regexp_replace(regexp_replace(col("items.snippet.topLevelComment.snippet.textDisplay"), ",", ";"), "\s+", " ").alias("textDisplay"),
        ).groupBy("channelId", "videoId").agg(collect_list("textDisplay").alias("Comments")).drop_duplicates(['videoId'])
    Comments_Data = Comments_Data.withColumn("Comments", concat_ws(", ", Comments_Data["Comments"]))
    
    return Comments_Data


Channel_Data_Transformed = process_channel_data(channel_df)
Video_Data_Transformed = process_videos_data(videos_df)
Comments_Data_Transformed = process_comments_data(comments_df)   

def write_to_s3(df, path_suffix, format_type="csv"):
    dynamic_frame = DynamicFrame.fromDF(df, glueContext, "dynamic_frame")
    glueContext.write_dynamic_frame.from_options(
        frame=dynamic_frame,
        connection_type="s3",
        connection_options={"path": f"s3://airline-data-ingestion-temp/transformed_data/{path_suffix}/"},
        format=format_type
    )


write_to_s3(Channel_Data_Transformed, "channel_data/channel_transformed_{}".format(datetime.now().strftime("%Y-%m-%d")), 'csv')
write_to_s3(Video_Data_Transformed, "video_data/video_transformed_{}".format(datetime.now().strftime("%Y-%m-%d")), 'csv')
write_to_s3(Comments_Data_Transformed, "comments_data/comments_transformed_{}".format(datetime.now().strftime("%Y-%m-%d")), 'csv')



def move_files_to_processed(bucket_name,from_folders,to_folders):

    s3 = boto3.client('s3')
    for from_folder,to_folder in zip(from_folders,to_folders):
        objects = s3.list_objects_v2(Bucket=bucket_name, Prefix=f"{from_folder}/")
        for obj in objects.get('Contents', []):
            key = obj['Key']
            new_key = key.replace(from_folder, to_folder)
            s3.copy_object(CopySource={'Bucket': bucket_name, 'Key': key}, Bucket=bucket_name, Key=new_key)
            s3.delete_object(Bucket=bucket_name, Key=key)
            print(f'Moved file from {key} to {new_key}')

bucket_name = 'airline-data-ingestion-temp'
from_folders = ['raw_data/to_processed/channel_data', 
                'raw_data/to_processed/video_data', 
                'raw_data/to_processed/comments_data']
to_folders = ['raw_data/processed/channel_data', 
              'raw_data/processed/video_data', 
              'raw_data/processed/comments_data']

move_files_to_processed(bucket_name, from_folders, to_folders)


job.commit()