output "elastic_ip" {
  description = "Public Elastic IP. Bookmark http://<this>:8000 until a domain exists."
  value       = aws_eip.prod.public_ip
}

output "instance_id" {
  value = aws_instance.prod.id
}

output "game_url" {
  value = "http://${aws_eip.prod.public_ip}:8000"
}

output "ssh_example" {
  description = "SSH command if you set key_name and ssh_cidr."
  value       = var.key_name == null ? null : "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${aws_eip.prod.public_ip}"
}
