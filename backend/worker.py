import os, json
import boto3

dynamodb = boto3.resource("dynamodb")
orders_tbl = dynamodb.Table(os.environ.get("ORDERS_TABLE", "Orders"))

def confirm_order(order_id: str):
    orders_tbl.update_item(
        Key={"orderId": order_id},
        UpdateExpression="SET #s = :v",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":v": "CONFIRMED"}
    )

# Example test run:
if __name__ == "__main__":
    confirm_order("PASTE_ORDER_ID_HERE")
    print("Updated to CONFIRMED")