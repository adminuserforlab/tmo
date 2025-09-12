<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NPC Health Check Report - {{ inventory_hostname }}</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 20px;
      background-color: #f9f9f9;
    }
    h1, h2 {
      text-align: center;
    }
    .summary {
      border: 2px solid #444;
      background: #fff3cd;
      padding: 10px;
      margin-bottom: 20px;
    }
    .ok {
      color: green;
      font-weight: bold;
    }
    .notok {
      color: red;
      font-weight: bold;
    }
    .warn {
      color: orange;
      font-weight: bold;
    }
    .section {
      background: #fff;
      border: 1px solid #ddd;
      border-radius: 6px;
      margin-bottom: 15px;
      padding: 10px;
      box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    .section h3 {
      margin: 0 0 10px;
      padding: 8px;
      background: #f0f0f0;
      border-radius: 4px;
    }
    .scroll-box {
      max-height: 200px;
      overflow-y: auto;
      background: #fefefe;
      padding: 8px;
      border: 1px solid #ddd;
      border-radius: 4px;
      font-family: monospace;
      white-space: pre-wrap;
    }
  </style>
</head>
<body>

<h1>NPC Health Check Report</h1>
<h2>Host: {{ inventory_hostname }}</h2>
<p><b>Generated at:</b> {{ report_timestamp }}</p>

<div class="summary">
  <h2>⚠️ Summary of Issues</h2>
  <ul>
    {% if failed_services.stdout_lines %}
      <li class="notok">Failed Services: {{ failed_services.stdout_lines|length }}</li>
    {% else %}
      <li class="ok">Failed Services: None</li>
    {% endif %}

    {% if not_ready_pods.stdout_lines %}
      <li class="notok">Pods Not Ready: {{ not_ready_pods.stdout_lines|length }}</li>
    {% else %}
      <li class="ok">Pods Not Ready: None</li>
    {% endif %}

    {% if zero_ready_pods_fact %}
      <li class="warn">Pods with 0/ Ready: Found</li>
    {% else %}
      <li class="ok">Pods with 0/ Ready: None</li>
    {% endif %}

    {% if warnings.stdout %}
      <li class="warn">K8s Warnings: Present</li>
    {% else %}
      <li class="ok">K8s Warnings: None</li>
    {% endif %}
  </ul>
</div>

<!-- Sections -->

<div class="section">
  <h3>Failed Services</h3>
  {% if failed_services.stdout_lines %}
    <span class="notok">Not OK</span>
    <div class="scroll-box">{{ failed_services.stdout }}</div>
  {% else %}
    <span class="ok">OK</span>
  {% endif %}
</div>

<div class="section">
  <h3>Pods Not Ready</h3>
  {% if not_ready_pods.stdout_lines %}
    <span class="notok">Not OK</span>
    <div class="scroll-box">{{ not_ready_pods.stdout }}</div>
  {% else %}
    <span class="ok">OK</span>
  {% endif %}
</div>

<div class="section">
  <h3>Zero Ready Pods</h3>
  {% if zero_ready_pods_fact %}
    <span class="warn">Warning</span>
    <div class="scroll-box">{{ zero_ready_pods_fact }}</div>
  {% else %}
    <span class="ok">OK</span>
  {% endif %}
</div>

<div class="section">
  <h3>Kubernetes Warnings</h3>
  {% if warnings.stdout %}
    <span class="warn">Warning</span>
    <div class="scroll-box">{{ warnings.stdout }}</div>
  {% else %}
    <span class="ok">OK</span>
  {% endif %}
</div>

<div class="section">
  <h3>Top Pods by CPU</h3>
  <div class="scroll-box">{{ top_pods_cpu_fact }}</div>
</div>

<div class="section">
  <h3>Top Nodes by CPU</h3>
  <div class="scroll-box">{{ top_nodes_cpu.stdout }}</div>
</div>

<div class="section">
  <h3>NPC Pods Status</h3>
  <div class="scroll-box">{{ npc_pods.stdout }}</div>
</div>

<div class="section">
  <h3>Database States</h3>
  <div class="scroll-box">{{ db_states.stdout }}</div>
</div>

<div class="section">
  <h3>System Preferences</h3>
  <div class="scroll-box">{{ system_prefs.stdout }}</div>
</div>

<div class="section">
  <h3>Alarm Check Results</h3>
  {% for item in alarm_results.results %}
    <h4>Namespace: {{ item.item }}</h4>
    <div class="scroll-box">{{ item.stdout }}</div>
  {% endfor %}
</div>

<div class="section">
  <h3>RSV Check Results</h3>
  {% for item in rsv_results.results %}
    <h4>Namespace: {{ item.item }}</h4>
    <div class="scroll-box">{{ item.stdout }}</div>
  {% endfor %}
</div>

</body>
</html>
