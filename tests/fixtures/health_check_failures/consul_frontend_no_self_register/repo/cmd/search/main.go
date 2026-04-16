package main

import (
	"github.com/hashicorp/consul/api"
)

func main() {
	client, _ := api.NewClient(api.DefaultConfig())
	client.Agent().ServiceRegister(&api.AgentServiceRegistration{Name: "search"})
}
