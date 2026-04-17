# Look up the image
data "openstack_images_image_v2" "ubuntu" {
  name        = var.image
  most_recent = true
}

# Node 1: control plane + worker
# Has both sharednet1 (external) and private network
resource "openstack_compute_instance_v2" "node1" {
  name      = "smartqueue-node1-${var.suffix}"
  image_id  = data.openstack_images_image_v2.ubuntu.id
  flavor_id = var.reservation_id
  key_pair  = var.key_pair

  # Public network (sharednet1) — provides external IP
  network {
    name = data.openstack_networking_network_v2.sharednet1.name
  }

  # Private network (fixed IP via pre-created port)
  network {
    port = openstack_networking_port_v2.private_port[0].id
  }

  security_groups = [
    "default",
    openstack_networking_secgroup_v2.allow_ssh.name,
    openstack_networking_secgroup_v2.allow_http.name,
    openstack_networking_secgroup_v2.allow_k8s.name,
  ]
}

# Node 2 & 3: workers
# Only private network — reach internet via NAT through node1
resource "openstack_compute_instance_v2" "workers" {
  count     = var.node_count - 1
  name      = "smartqueue-node${count.index + 2}-${var.suffix}"
  image_id  = data.openstack_images_image_v2.ubuntu.id
  flavor_id = var.reservation_id
  key_pair  = var.key_pair

  # Private network only
  network {
    port = openstack_networking_port_v2.private_port[count.index + 1].id
  }

}
