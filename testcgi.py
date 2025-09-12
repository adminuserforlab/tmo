<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NPC Health Check Report - {{ inventory_hostname }}</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 20px;
      background: #f9f9f9;
    }
    h1, h2 {
      color: #333;
    }
    .summary {
      padding: 15px;
      background: #eef5ff;
      border-left: 6px solid #007bff;
      margin-bottom: 20px;
    }
    .ok { color: green; font-weight: bold; }
    .not-ok { color: red; font-weight: bold; }
    .warn { color: orange; font-weight: bold; }
    .section {
      margin-bottom: 25px;
      background: #fff;
      padding: 10px;
      border-radius: 8px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.1);
    }
    .section h2 {
      margin-top: 0;
      font-size: 18px;
      border-bottom: 1px solid #ddd;
      padding-bottom: 5px;
    }
    .scroll-box {
      max-height: 200px;
      overflow-y: auto;
      background: #f4f4f4;
      padding: 10px;
      border: 1px solid #ccc;
      border-radius: 6px;
      font-family: monospace;
      font-size: 13px;
      white-space: pre-wrap;
    }
  </style>
</head>
<body>

  <h1>NPC Health Check Report</h1>
  <p><b>Host:</b> {{ inventory_hostname }}</p>
  <p><b>Date:</b> {{ ansible_date_time.date }} {{ ansible_date_time.time }}</p>

  <!-- ================= SUMMARY ================= -->
  <div class="summary">
    <h2>Summary</h2>
    <ul>
      <li>Failed Services: {% if failed_services.stdout %}<span class="not-ok">Not OK</span>{% else %}<span class="ok">OK</span>{% endif %}</li>
      <li>Non-Running Pods: {% if non_running_pods.stdout %}<span class="not-ok">Not OK</span>{% else %}<span class="ok">OK</span>{% endif %}</li>
      <li>Pods with 0/ Ready: {% if zero_ready_pods_fact %}<span class="not-ok">Not OK</span>{% else %}<span class="ok">OK</span>{% endif %}</li>
      <li>Warnings: {% if warnings.stdout %}<span class="warn">Warning</span>{% else %}<span class="ok">OK</span>{% endif %}</li>
      <li>Disk Usage: {% if disk_used.stdout|int > disk_threshold %}<span class="not-ok">High</span>{% else %}<span class="ok">OK</span>{% endif %}</li>
      <li>Memory Free: {% if mem_free_mb|int < mem_threshold %}<span class="not-ok">Low</span>{% else %}<span class="ok">OK</span>{% endif %}</li>
      <li>Load Avg: {% if ansible_load_avg.1|float > load_threshold %}<span class="not-ok">High</span>{% else %}<span class="ok">OK</span>{% endif %}</li>
    </ul>
  </div>

  <!-- ================= DETAILS ================= -->

  <div class="section">
    <h2>Failed Services</h2>
    <div class="scroll-box">{{ failed_services.stdout | default("None") }}</div>
  </div>

  <div class="section">
    <h2>Non-Running Pods</h2>
    <div class="scroll-box">{{ non_running_pods.stdout | default("None") }}</div>
  </div>

  <div class="section">
    <h2>Pods with 0/ Readiness</h2>
    <div class="scroll-box">{{ zero_ready_pods_fact | default("None") }}</div>
  </div>

  <div class="section">
    <h2>Kubernetes Warnings</h2>
    <div class="scroll-box">{{ warnings.stdout | default("None") }}</div>
  </div>

  <div class="section">
    <h2>Top Pods by CPU</h2>
    <div class="scroll-box">{{ top_pods_cpu_fact | default("None") }}</div>
  </div>

  <div class="section">
    <h2>Top Nodes by CPU</h2>
    <div class="scroll-box">{{ top_nodes_cpu.stdout | default("None") }}</div>
  </div>

  <div class="section">
    <h2>NPC SPS Status</h2>
    <div class="scroll-box">{{ sps_status.stdout | default("N/A") }}</div>
  </div>

  <div class="section">
    <h2>Network Status</h2>
    <div class="scroll-box">{{ net_status.stdout | default("N/A") }}</div>
  </div>

  <div class="section">
    <h2>XDR Status</h2>
    <div class="scroll-box">{{ xdr_status.stdout | default("N/A") }}</div>
  </div>

  <div class="section">
    <h2>Diameter Peers</h2>
    <div class="scroll-box">{{ diameter_peers.stdout | default("N/A") }}</div>
  </div>

  <div class="section">
    <h2>Diameter Routes</h2>
    <div class="scroll-box">{{ diameter_routes.stdout | default("N/A") }}</div>
  </div>

  <div class="section">
    <h2>DB States</h2>
    <div class="scroll-box">{{ db_states.stdout | default("N/A") }}</div>
  </div>

  <div class="section">
    <h2>Alarms</h2>
    <div class="scroll-box">
      {% for result in alarm_results.results %}
        <b>Namespace:</b> {{ result.item }}<br>
        {{ result.stdout | default("No alarms") }}<br><br>
      {% endfor %}
    </div>
  </div>

  <div class="section">
    <h2>RSV Check</h2>
    <div class="scroll-box">
      {% for result in rsv_results.results %}
        <b>Namespace:</b> {{ result.item }}<br>
        {{ result.stdout | default("No RSV issues") }}<br><br>
      {% endfor %}
    </div>
  </div>

</body>
</html>
