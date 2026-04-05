# Reference the existing Chameleon shared network
data "openstack_networking_network_v2" "sharednet1" {
  name = "sharednet1"
}

# Private network for internal cluster communication
resource "openstack_networking_network_v2" "private_net" {
  name           = "smartqueue-net-${var.suffix}"
  admin_state_up = true
}

resource "openstack_networking_subnet_v2" "private_subnet" {
  name            = "smartqueue-subnet-${var.suffix}"
  network_id      = openstack_networking_network_v2.private_net.id
  cidr            = var.private_subnet_cidr
  ip_version      = 4
  no_gateway      = true
  enable_dhcp     = true
  dns_nameservers = ["8.8.8.8", "8.8.4.4"]
}

# Security group: allow SSH
resource "openstack_networking_secgroup_v2" "allow_ssh" {
  name        = "allow-ssh-${var.suffix}"
  description = "Allow SSH access"
}

resource "openstack_networking_secgroup_rule_v2" "ssh_ingress" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 22
  port_range_max    = 22
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.allow_ssh.id
}

# Security group: allow HTTP/HTTPS
resource "openstack_networking_secgroup_v2" "allow_http" {
  name        = "allow-http-${var.suffix}"
  description = "Allow HTTP and HTTPS access"
}

resource "openstack_networking_secgroup_rule_v2" "http_ingress" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 80
  port_range_max    = 80
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.allow_http.id
}

resource "openstack_networking_secgroup_rule_v2" "https_ingress" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 443
  port_range_max    = 443
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.allow_http.id
}

# Security group: allow K8S API + NodePort range
resource "openstack_networking_secgroup_v2" "allow_k8s" {
  name        = "allow-k8s-${var.suffix}"
  description = "Allow K8S API server and NodePort services"
}

resource "openstack_networking_secgroup_rule_v2" "k8s_api" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 6443
  port_range_max    = 6443
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.allow_k8s.id
}

resource "openstack_networking_secgroup_rule_v2" "nodeport_range" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 30000
  port_range_max    = 32767
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.allow_k8s.id
}

# Ports on private network with fixed IPs and disabled port security
resource "openstack_networking_port_v2" "private_port" {
  count              = var.node_count
  name               = "smartqueue-port${count.index + 1}-${var.suffix}"
  network_id         = openstack_networking_network_v2.private_net.id
  no_security_groups = true
  port_security_enabled = false

  fixed_ip {
    subnet_id  = openstack_networking_subnet_v2.private_subnet.id
    ip_address = var.private_ips[count.index]
  }
}

# Floating IP for node1 (jump host)
resource "openstack_networking_floatingip_v2" "node1_fip" {
  pool = "public"
}

resource "openstack_compute_floatingip_associate_v2" "node1_fip_assoc" {
  floating_ip = openstack_networking_floatingip_v2.node1_fip.address
  instance_id = openstack_compute_instance_v2.nodes[0].id
}
