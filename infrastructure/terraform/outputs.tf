output "bucket_name" {
  value = google_storage_bucket.flight_data.name
}

output "service_account_email" {
  value = google_service_account.flight_search_storage.email
}
