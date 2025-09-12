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
    h1 {
      text-align: center;
      color: #333;
    }
    table {
      border-collapse: collapse;
      width: 100%;
      margin-bottom: 25px;
      box-shadow: 0 2px 5px rgba(0,0,0,0.1);
      background: #fff;
    }
    th, td {
      border: 1px solid #ccc;
      padding: 8px 12px;
      text-align: left;
    }
    th {
      background: #444;
      color: #fff;
    }
    tr:nth-child(even) {
      background: #f2f2f2;
    }
    .ok {
      color: green;
      font-weight: bold;
    }
    .not-ok {
      color: red;
      font-weight: bold;
    }
    .section {
      margin-top: 30px;
    }
    pre {
      background: #f4f4f4;
      padding: 10px;
      border-radius: 4px;
      overflow-x: auto;
    }
  </style>
</head>
<body>
  <h1>NPC Health Check Report</h1>
  <p><b>Host:</b> {{ inventory_hostname }}</p>
  <p><b>Generated:</b> {{ report_timestamp }}</p>

  <div class="section">
    <h2>System Services</h2>
    <table>
      <tr><th>Check</th><th>Status</th><th>Details</th></tr>
      <tr>
        <td>Failed Services</td>
        <td>
          {% if failed_services.stdout_lines|length > 0 %}
            <span class="not-ok">Not OK</span>
          {% else %}
            <span class="ok">OK</span>
          {% endif %}
        </td>
        <td>
          {% for s in failed_services.stdout_lines %}
            <pre>{{ s }}</pre>
          {% endfor %}
        </td>
      </tr>
      <tr>
        <td>Disk Usage</td>
        <td>
          {% if disk_used.stdout|int > disk_threshold %}
            <span class="not-ok">Not OK</span>
          {% else %}
            <span class="ok">OK</span>
          {% endif %}
        </td>
        <td>{{ disk_used.stdout }} % Used (Threshold: {{ disk_threshold }}%)</td>
      </tr>
      <tr>
        <td>Memory Free</td>
        <td>
          {% if mem_free_mb|int < mem_threshold %}
            <span class="not-ok">Not OK</span>
          {% else %}
            <span class="ok">OK</span>
          {% endif %}
        </td>
        <td>{{ mem_free_mb }} MB Free (Threshold: {{ mem_threshold }} MB)</td>
      </tr>
    </table>
  </div>

  <div class="section">
    <h2>Kubernetes Pods</h2>
    <table>
      <tr><th>Check</th><th>Status</th><th>Details</th></tr>
      <tr>
        <td>Non-Running Pods</td>
        <td>
          {% if non_running_pods.stdout_lines|length > 0 %}
            <span class="not-ok">Not OK</span>
          {% else %}
            <span class="ok">OK</span>
          {% endif %}
        </td>
        <td>
          {% for pod in non_running_pods.stdout_lines %}
            <pre>{{ pod }}</pre>
          {% endfor %}
        </td>
      </tr>
      <tr>
        <td>Zero Ready Pods</td>
        <td>
          {% if zero_ready_pods.stdout|length > 0 %}
            <span class="not-ok">Not OK</span>
          {% else %}
            <span class="ok">OK</span>
          {% endif %}
        </td>
        <td><pre>{{ zero_ready_pods_fact }}</pre></td>
      </tr>
      <tr>
        <td>NPC Pods Status</td>
        <td><span class="ok">Collected</span></td>
        <td>
          {% for line in npc_pods.stdout_lines %}
            <pre>{{ line }}</pre>
          {% endfor %}
        </td>
      </tr>
    </table>
  </div>

  <div class="section">
    <h2>Cluster Metrics</h2>
    <table>
      <tr><th>Check</th><th>Status</th><th>Details</th></tr>
      <tr>
        <td>Top Pods (CPU)</td>
        <td><span class="ok">Collected</span></td>
        <td><pre>{{ top_pods_cpu_fact }}</pre></td>
      </tr>
      <tr>
        <td>Top Nodes (CPU)</td>
        <td><span class="ok">Collected</span></td>
        <td>
          {% for line in top_nodes_cpu.stdout_lines %}
            <pre>{{ line }}</pre>
          {% endfor %}
        </td>
      </tr>
    </table>
  </div>

  <div class="section">
    <h2>Errors & Warnings</h2>
    <table>
      <tr><th>Type</th><th>Details</th></tr>
      <tr>
        <td>Journalctl Errors</td>
        <td>
          {% for e in error_logs.stdout_lines %}
            <pre>{{ e }}</pre>
          {% endfor %}
        </td>
      </tr>
      <tr>
        <td>K8s Warnings</td>
        <td>
          {% for w in warnings.stdout_lines %}
            <pre>{{ w }}</pre>
          {% endfor %}
        </td>
      </tr>
    </table>
  </div>
</body>
</html>
