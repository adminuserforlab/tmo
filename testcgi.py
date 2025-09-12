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
    h2 {
      margin-top: 25px;
      color: #444;
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
      padding: 6px 10px;
      text-align: left;
      font-size: 14px;
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
    .warning {
      color: orange;
      font-weight: bold;
    }
    .summary-box {
      background: #fff;
      border: 1px solid #ccc;
      padding: 10px;
      margin-bottom: 20px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .scroll-box {
      max-height: 120px;
      overflow-y: auto;
      background: #f9f9f9;
      border: 1px solid #ddd;
      padding: 6px;
      margin-top: 5px;
      font-size: 13px;
    }
    pre {
      background: #f4f4f4;
      padding: 5px;
      border-radius: 4px;
      overflow-x: auto;
      margin: 2px 0;
    }
  </style>
</head>
<body>
  <h1>NPC Health Check Report</h1>
  <p><b>Host:</b> {{ inventory_hostname }}</p>
  <p><b>Generated:</b> {{ report_timestamp }}</p>

  <!-- 🔹 Quick Summary -->
  <div class="summary-box">
    <h2>🚦 Quick Summary (Issues Only)</h2>
    <table>
      <tr>
        <th>Category</th>
        <th>Status</th>
        <th>Details</th>
      </tr>
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
          {% if failed_services.stdout_lines|length > 0 %}
            <div class="scroll-box">
              {% for s in failed_services.stdout_lines %}
                <pre>{{ s }}</pre>
              {% endfor %}
            </div>
          {% endif %}
        </td>
      </tr>
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
          {% if non_running_pods.stdout_lines|length > 0 %}
            <div class="scroll-box">
              {% for pod in non_running_pods.stdout_lines %}
                <pre>{{ pod }}</pre>
              {% endfor %}
            </div>
          {% endif %}
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
        <td>
          {% if zero_ready_pods.stdout|length > 0 %}
            <div class="scroll-box"><pre>{{ zero_ready_pods_fact }}</pre></div>
          {% endif %}
        </td>
      </tr>
      <tr>
        <td>K8s Warnings</td>
        <td>
          {% if warnings.stdout_lines|length > 0 %}
            <span class="warning">Warning</span>
          {% else %}
            <span class="ok">OK</span>
          {% endif %}
        </td>
        <td>
          {% if warnings.stdout_lines|length > 0 %}
            <div class="scroll-box">
              {% for w in warnings.stdout_lines %}
                <pre>{{ w }}</pre>
              {% endfor %}
            </div>
          {% endif %}
        </td>
      </tr>
      <tr>
        <td>Journalctl Errors</td>
        <td>
          {% if error_logs.stdout_lines|length > 0 %}
            <span class="not-ok">Not OK</span>
          {% else %}
            <span class="ok">OK</span>
          {% endif %}
        </td>
        <td>
          {% if error_logs.stdout_lines|length > 0 %}
            <div class="scroll-box">
              {% for e in error_logs.stdout_lines %}
                <pre>{{ e }}</pre>
              {% endfor %}
            </div>
          {% endif %}
        </td>
      </tr>
    </table>
  </div>

  <!-- 🔹 Full Details Below -->
  <h2>Detailed Results</h2>
  <p>See below for full task outputs and checks.</p>

  <!-- You can keep the existing long tables for services, pods, nodes, metrics here -->
  <!-- (reuse the ones from the earlier template you liked) -->

</body>
</html>
