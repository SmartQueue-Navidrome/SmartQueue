output "floating_ip" {
  description = "Public floating IP for node1 (jump host)"
  value       = openstack_networking_floatingip_v2.node1_fip.address
}

output "node1_sharednet_ip" {
  description = "Node1 IP on sharednet1"
  value       = openstack_compute_instance_v2.node1.network[0].fixed_ip_v4
}

output "node_private_ips" {
  description = "Private IPs for all nodes"
  value       = var.private_ips
}

output "node1_id" {
  description = "Node1 instance ID"
  value       = openstack_compute_instance_v2.node1.id
}

output "worker_ids" {
  description = "Worker instance IDs"
  value       = openstack_compute_instance_v2.workers[*].id
}

output "volume_devices" {
  description = "Block volume attachment device paths"
  value = {
    navidrome = openstack_compute_volume_attach_v2.navidrome_attach.device
    postgres  = openstack_compute_volume_attach_v2.postgres_attach.device
  }
}
