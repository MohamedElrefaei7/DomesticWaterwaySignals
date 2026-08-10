output "instance_id" {
  description = "EC2 instance ID."
  value       = aws_instance.main.id
}

output "eip_address" {
  description = "Public IP address (Elastic IP) of the instance."
  value       = aws_eip.main.public_ip
}

output "data_volume_id" {
  description = "EBS volume ID of the separate, persistent data volume."
  value       = aws_ebs_volume.data.id
}
