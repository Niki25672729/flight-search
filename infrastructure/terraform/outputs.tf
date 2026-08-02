output "bucket_name" {
  value = google_storage_bucket.flight_data.name
}

output "service_account_email" {
  value = google_service_account.flight_search_storage.email
}

output "bigquery_dataset_id" {
  value = google_bigquery_dataset.flight_search.dataset_id
}
