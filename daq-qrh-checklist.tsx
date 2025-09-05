import React, { useState } from 'react';
import { ChevronDown, ChevronRight, AlertCircle, CheckCircle, Settings, Database, Activity, Globe, FileText, Zap } from 'lucide-react';

const QRHChecklist = () => {
  const [activeTab, setActiveTab] = useState('overview');
  const [expandedSections, setExpandedSections] = useState({});
  const [checkedItems, setCheckedItems] = useState({});

  const toggleSection = (section) => {
    setExpandedSections(prev => ({
      ...prev,
      [section]: !prev[section]
    }));
  };

  const toggleCheckItem = (itemId) => {
    setCheckedItems(prev => ({
      ...prev,
      [itemId]: !prev[itemId]
    }));
  };

  const ChecklistItem = ({ id, text, note, warning }) => (
    <div className="flex items-start space-x-3 py-2 px-4 hover:bg-gray-50 border-l-2 border-gray-200">
      <div 
        className={`w-5 h-5 rounded border-2 flex items-center justify-center cursor-pointer mt-0.5 ${
          checkedItems[id] ? 'bg-green-600 border-green-600' : 'border-gray-300 hover:border-green-400'
        }`}
        onClick={() => toggleCheckItem(id)}
      >
        {checkedItems[id] && <CheckCircle className="w-3 h-3 text-white" />}
      </div>
      <div className="flex-1">
        <div className={`font-mono text-sm ${checkedItems[id] ? 'line-through text-gray-500' : 'text-gray-800'}`}>
          {text}
        </div>
        {note && <div className="text-xs text-blue-600 mt-1">{note}</div>}
        {warning && (
          <div className="flex items-center text-xs text-red-600 mt-1">
            <AlertCircle className="w-3 h-3 mr-1" />
            {warning}
          </div>
        )}
      </div>
    </div>
  );

  const SectionHeader = ({ title, icon: Icon, isExpanded, onClick, status }) => (
    <div 
      className="flex items-center justify-between py-3 px-4 bg-gray-100 border-b cursor-pointer hover:bg-gray-200"
      onClick={onClick}
    >
      <div className="flex items-center space-x-3">
        <Icon className="w-5 h-5 text-blue-600" />
        <span className="font-semibold text-gray-800">{title}</span>
        {status && (
          <span className={`px-2 py-1 text-xs rounded font-mono ${
            status === 'RUNNING' ? 'bg-green-100 text-green-800' :
            status === 'CAUTION' ? 'bg-yellow-100 text-yellow-800' :
            'bg-red-100 text-red-800'
          }`}>
            {status}
          </span>
        )}
      </div>
      {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
    </div>
  );

  const tabs = [
    { id: 'overview', name: 'SYSTEM OVERVIEW', icon: FileText },
    { id: 'startup', name: 'STARTUP PROCEDURES', icon: Zap },
    { id: 'normal', name: 'NORMAL OPERATIONS', icon: Settings },
    { id: 'monitoring', name: 'SYSTEM MONITORING', icon: Activity },
    { id: 'emergency', name: 'EMERGENCY PROCEDURES', icon: AlertCircle }
  ];

  const renderOverview = () => (
    <div className="space-y-4">
      <div className="bg-blue-50 border border-blue-200 rounded p-4">
        <h3 className="font-bold text-blue-800 mb-2">DAQ SYSTEM STATUS</h3>
        <div className="grid grid-cols-2 gap-4 text-sm font-mono">
          <div>Server IP: <span className="text-blue-600">3.98.181.12</span></div>
          <div>Static IP: <span className="text-green-600">ASSIGNED</span></div>
          <div>RAM Limit: <span className="text-yellow-600">1GB/Container</span></div>
          <div>CPU Limit: <span className="text-yellow-600">1 Core/Container</span></div>
        </div>
      </div>

      <div className="space-y-2">
        <SectionHeader 
          title="MONGODB DATABASE" 
          icon={Database} 
          isExpanded={expandedSections.mongodb}
          onClick={() => toggleSection('mongodb')}
          status="RUNNING"
        />
        {expandedSections.mongodb && (
          <div className="bg-white border">
            <div className="p-4 font-mono text-xs space-y-1">
              <div>PORT: 3000</div>
              <div>URL: http://3.98.181.12:3000</div>
              <div>USERNAME: admin</div>
              <div>PASSWORD: admin123</div>
              <div>STATUS: pm2 status</div>
            </div>
          </div>
        )}

        <SectionHeader 
          title="INFLUXDB 2.7" 
          icon={Database} 
          isExpanded={expandedSections.influx27}
          onClick={() => toggleSection('influx27')}
          status="RUNNING"
        />
        {expandedSections.influx27 && (
          <div className="bg-white border">
            <div className="p-4 font-mono text-xs space-y-1">
              <div>PORT: 8086</div>
              <div>URL: http://3.98.181.12:8086</div>
              <div>USERNAME: admin</div>
              <div>PASSWORD: turbo-charged-falcon-machine</div>
              <div>ORG: WFR</div>
              <div>BUCKET: ourCar</div>
            </div>
          </div>
        )}

        <SectionHeader 
          title="GRAFANA DASHBOARD" 
          icon={Activity} 
          isExpanded={expandedSections.grafana}
          onClick={() => toggleSection('grafana')}
          status="RUNNING"
        />
        {expandedSections.grafana && (
          <div className="bg-white border">
            <div className="p-4 font-mono text-xs space-y-1">
              <div>PORT: 8087</div>
              <div>URL: http://3.98.181.12:8087</div>
              <div>USERNAME: admin</div>
              <div>PASSWORD: turbo-charged-plotting-machine</div>
            </div>
          </div>
        )}

        <SectionHeader 
          title="FRONTEND APPLICATION" 
          icon={Globe} 
          isExpanded={expandedSections.frontend}
          onClick={() => toggleSection('frontend')}
          status="RUNNING"
        />
        {expandedSections.frontend && (
          <div className="bg-white border">
            <div className="p-4 font-mono text-xs space-y-1">
              <div>PORT: 8060</div>
              <div>URL: http://3.98.181.12:8060</div>
              <div>DEPLOYMENT: GitHub Actions Auto-Deploy</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );

  const renderStartup = () => (
    <div className="space-y-4">
      <div className="bg-red-50 border border-red-200 rounded p-4">
        <div className="flex items-center space-x-2 mb-2">
          <AlertCircle className="w-5 h-5 text-red-600" />
          <h3 className="font-bold text-red-800">CAUTION</h3>
        </div>
        <ul className="text-sm text-red-700 space-y-1">
          <li>• DO NOT SSH WITH VSCODE</li>
          <li>• Always set RAM and CPU limits on new containers</li>
          <li>• Do not exceed 1GB RAM / 1 CPU per container</li>
        </ul>
      </div>

      <div className="space-y-2">
        <SectionHeader 
          title="INITIAL SERVER ACCESS" 
          icon={Settings} 
          isExpanded={expandedSections.serverAccess}
          onClick={() => toggleSection('serverAccess')}
        />
        {expandedSections.serverAccess && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="ssh1" 
              text="Obtain private key from DAQ team lead"
              note="Required for SSH access to server"
            />
            <ChecklistItem 
              id="ssh2" 
              text="Create SSH shortcut configuration"
              note="Host: 3.98.181.12"
            />
            <ChecklistItem 
              id="ssh3" 
              text="Test SSH connection to server"
              warning="Use terminal SSH only - no VSCode SSH"
            />
          </div>
        )}

        <SectionHeader 
          title="DOCKER CONTAINER STARTUP" 
          icon={Settings} 
          isExpanded={expandedSections.dockerStartup}
          onClick={() => toggleSection('dockerStartup')}
        />
        {expandedSections.dockerStartup && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="docker1" 
              text="Verify Docker service status"
              note="sudo systemctl status docker"
            />
            <ChecklistItem 
              id="docker2" 
              text="Check existing container status"
              note="docker ps -a"
            />
            <ChecklistItem 
              id="docker3" 
              text="Start InfluxDB container"
              note="Container: influxwfr"
            />
            <ChecklistItem 
              id="docker4" 
              text="Start MongoDB container"
              note="Check pm2 status after startup"
            />
            <ChecklistItem 
              id="docker5" 
              text="Start Grafana container"
              note="Container: grafana"
            />
            <ChecklistItem 
              id="docker6" 
              text="Start Frontend container"
              note="Container: frontend"
            />
            <ChecklistItem 
              id="docker7" 
              text="Verify datalink network connectivity"
              warning="Required for inter-container communication"
            />
          </div>
        )}

        <SectionHeader 
          title="SYSTEM VERIFICATION" 
          icon={CheckCircle} 
          isExpanded={expandedSections.systemVerify}
          onClick={() => toggleSection('systemVerify')}
        />
        {expandedSections.systemVerify && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="verify1" 
              text="Test InfluxDB web interface (port 8086)"
              note="Login with admin credentials"
            />
            <ChecklistItem 
              id="verify2" 
              text="Test Grafana dashboard (port 8087)"
              note="Verify data source connectivity"
            />
            <ChecklistItem 
              id="verify3" 
              text="Test frontend application (port 8060)"
              note="Check for proper data display"
            />
            <ChecklistItem 
              id="verify4" 
              text="Verify MongoDB connection (port 3000)"
              note="Check pm2 logs for errors"
            />
          </div>
        )}
      </div>
    </div>
  );

  const renderNormal = () => (
    <div className="space-y-4">
      <div className="space-y-2">
        <SectionHeader 
          title="CONTAINER MANAGEMENT" 
          icon={Settings} 
          isExpanded={expandedSections.containerMgmt}
          onClick={() => toggleSection('containerMgmt')}
        />
        {expandedSections.containerMgmt && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="mgmt1" 
              text="Check container resource limits"
              note="docker inspect CONTAINER_NAME --format='{{ .HostConfig.Memory }}'"
            />
            <ChecklistItem 
              id="mgmt2" 
              text="Update container limits if needed"
              note="docker update --memory=1g --memory-swap=1.5g CONTAINER_NAME"
            />
            <ChecklistItem 
              id="mgmt3" 
              text="Monitor container logs"
              note="docker logs -f CONTAINER_NAME"
            />
            <ChecklistItem 
              id="mgmt4" 
              text="Restart containers as needed"
              warning="Use docker restart, not docker stop/start"
            />
          </div>
        )}

        <SectionHeader 
          title="DATA OPERATIONS" 
          icon={Database} 
          isExpanded={expandedSections.dataOps}
          onClick={() => toggleSection('dataOps')}
        />
        {expandedSections.dataOps && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="data1" 
              text="Monitor data ingestion rates"
              note="Check InfluxDB write statistics"
            />
            <ChecklistItem 
              id="data2" 
              text="Verify MongoDB collections"
              note="Use MongoDB admin interface"
            />
            <ChecklistItem 
              id="data3" 
              text="Check disk space utilization"
              note="df -h command on server"
            />
            <ChecklistItem 
              id="data4" 
              text="Review Grafana dashboards"
              note="Verify real-time data visualization"
            />
          </div>
        )}

        <SectionHeader 
          title="NETWORK OPERATIONS" 
          icon={Globe} 
          isExpanded={expandedSections.networkOps}
          onClick={() => toggleSection('networkOps')}
        />
        {expandedSections.networkOps && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="net1" 
              text="Verify datalink network status"
              note="docker network ls"
            />
            <ChecklistItem 
              id="net2" 
              text="Test inter-container connectivity"
              note="Ping between containers on datalink network"
            />
            <ChecklistItem 
              id="net3" 
              text="Monitor external port accessibility"
              note="Test all service ports: 3000, 8050, 8060, 8086, 8087, 9000"
            />
          </div>
        )}
      </div>
    </div>
  );

  const renderMonitoring = () => (
    <div className="space-y-4">
      <div className="space-y-2">
        <SectionHeader 
          title="SYSTEM HEALTH MONITORING" 
          icon={Activity} 
          isExpanded={expandedSections.healthMon}
          onClick={() => toggleSection('healthMon')}
        />
        {expandedSections.healthMon && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="health1" 
              text="Check system memory usage"
              note="free -h command"
            />
            <ChecklistItem 
              id="health2" 
              text="Monitor CPU utilization"
              note="htop or top command"
            />
            <ChecklistItem 
              id="health3" 
              text="Verify swap memory allocation"
              note="1GB swap currently allocated"
            />
            <ChecklistItem 
              id="health4" 
              text="Check disk I/O performance"
              note="iostat command if available"
            />
          </div>
        )}

        <SectionHeader 
          title="SERVICE MONITORING" 
          icon={Settings} 
          isExpanded={expandedSections.serviceMon}
          onClick={() => toggleSection('serviceMon')}
        />
        {expandedSections.serviceMon && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="service1" 
              text="Check PM2 process status"
              note="pm2 status"
            />
            <ChecklistItem 
              id="service2" 
              text="Review PM2 logs"
              note="pm2 logs"
            />
            <ChecklistItem 
              id="service3" 
              text="Monitor Docker container health"
              note="docker stats for real-time metrics"
            />
            <ChecklistItem 
              id="service4" 
              text="Check systemd service status"
              note="systemctl status lap.service"
            />
          </div>
        )}

        <SectionHeader 
          title="ERROR LOG MONITORING" 
          icon={AlertCircle} 
          isExpanded={expandedSections.errorMon}
          onClick={() => toggleSection('errorMon')}
        />
        {expandedSections.errorMon && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="error1" 
              text="Review Docker error logs"
              note="tail -f /var/log/docker-errors/*.err.log"
            />
            <ChecklistItem 
              id="error2" 
              text="Check systemd journal for errors"
              note="journalctl -u docker-error-logger.service"
            />
            <ChecklistItem 
              id="error3" 
              text="Monitor InfluxDB query performance"
              note="Check for slow query warnings"
            />
            <ChecklistItem 
              id="error4" 
              text="Review application-specific logs"
              note="Check each service's individual log files"
            />
          </div>
        )}
      </div>
    </div>
  );

  const renderEmergency = () => (
    <div className="space-y-4">
      <div className="bg-red-50 border border-red-200 rounded p-4">
        <div className="flex items-center space-x-2 mb-2">
          <AlertCircle className="w-5 h-5 text-red-600" />
          <h3 className="font-bold text-red-800">EMERGENCY NOTICE</h3>
        </div>
        <p className="text-sm text-red-700">
          If server fails completely, daily backups are available. Do not panic.
        </p>
      </div>

      <div className="space-y-2">
        <SectionHeader 
          title="CONTAINER FAILURE RECOVERY" 
          icon={AlertCircle} 
          isExpanded={expandedSections.containerFailure}
          onClick={() => toggleSection('containerFailure')}
        />
        {expandedSections.containerFailure && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="fail1" 
              text="Identify failed container"
              note="docker ps -a | grep -v Up"
            />
            <ChecklistItem 
              id="fail2" 
              text="Check container logs for errors"
              note="docker logs CONTAINER_NAME"
            />
            <ChecklistItem 
              id="fail3" 
              text="Attempt container restart"
              note="docker restart CONTAINER_NAME"
            />
            <ChecklistItem 
              id="fail4" 
              text="If restart fails, recreate container"
              warning="Use documented run commands from QRH"
            />
            <ChecklistItem 
              id="fail5" 
              text="Reconnect to datalink network"
              note="docker network connect datalink CONTAINER_NAME"
            />
          </div>
        )}

        <SectionHeader 
          title="DATA CORRUPTION/LOSS" 
          icon={Database} 
          isExpanded={expandedSections.dataLoss}
          onClick={() => toggleSection('dataLoss')}
        />
        {expandedSections.dataLoss && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="loss1" 
              text="Stop all data ingestion immediately"
              warning="Prevent further corruption"
            />
            <ChecklistItem 
              id="loss2" 
              text="Assess extent of data loss"
              note="Check InfluxDB and MongoDB integrity"
            />
            <ChecklistItem 
              id="loss3" 
              text="Restore from daily backup if available"
              note="Contact team lead for backup location"
            />
            <ChecklistItem 
              id="loss4" 
              text="Delete corrupted data using date ranges"
              note="Use documented delete commands"
            />
          </div>
        )}

        <SectionHeader 
          title="NETWORK/CONNECTIVITY ISSUES" 
          icon={Globe} 
          isExpanded={expandedSections.networkIssues}
          onClick={() => toggleSection('networkIssues')}
        />
        {expandedSections.networkIssues && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="netissue1" 
              text="Check external network connectivity"
              note="ping google.com"
            />
            <ChecklistItem 
              id="netissue2" 
              text="Verify Docker network status"
              note="docker network inspect datalink"
            />
            <ChecklistItem 
              id="netissue3" 
              text="Restart Docker daemon if needed"
              warning="sudo systemctl restart docker"
            />
            <ChecklistItem 
              id="netissue4" 
              text="Recreate datalink network"
              note="docker network create datalink"
            />
            <ChecklistItem 
              id="netissue5" 
              text="Reconnect all containers to network"
              note="Execute network connect commands"
            />
          </div>
        )}

        <SectionHeader 
          title="SYSTEM CLEANUP PROCEDURES" 
          icon={Settings} 
          isExpanded={expandedSections.cleanup}
          onClick={() => toggleSection('cleanup')}
        />
        {expandedSections.cleanup && (
          <div className="bg-white border space-y-0">
            <ChecklistItem 
              id="clean1" 
              text="Clear Docker build cache"
              note="sudo docker builder prune"
            />
            <ChecklistItem 
              id="clean2" 
              text="Remove dangling images"
              note="sudo docker image prune"
            />
            <ChecklistItem 
              id="clean3" 
              text="Remove unused volumes (optional)"
              note="sudo docker volume prune"
              warning="Use with caution - may remove data"
            />
            <ChecklistItem 
              id="clean4" 
              text="Check and rotate log files"
              note="Review logrotate configuration"
            />
          </div>
        )}
      </div>
    </div>
  );

  const renderContent = () => {
    switch (activeTab) {
      case 'overview': return renderOverview();
      case 'startup': return renderStartup();
      case 'normal': return renderNormal();
      case 'monitoring': return renderMonitoring();
      case 'emergency': return renderEmergency();
      default: return renderOverview();
    }
  };

  return (
    <div className="max-w-6xl mx-auto bg-white shadow-2xl">
      {/* Header */}
      <div className="bg-blue-900 text-white p-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">WESTERN FORMULA RACING</h1>
            <h2 className="text-lg">DATA ACQUISITION SYSTEM</h2>
            <h3 className="text-sm opacity-80">QUICK REFERENCE HANDBOOK</h3>
          </div>
          <div className="text-right text-sm font-mono">
            <div>REV: 2025.1</div>
            <div>SERVER: 3.98.181.12</div>
            <div className="flex items-center space-x-1">
              <div className="w-2 h-2 bg-green-400 rounded-full"></div>
              <span>OPERATIONAL</span>
            </div>
          </div>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="flex border-b bg-gray-50">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center space-x-2 px-4 py-3 border-b-2 font-semibold text-sm transition-colors ${
              activeTab === tab.id
                ? 'border-blue-600 text-blue-600 bg-white'
                : 'border-transparent text-gray-600 hover:text-blue-600 hover:border-gray-300'
            }`}
          >
            <tab.icon className="w-4 h-4" />
            <span>{tab.name}</span>
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="min-h-screen p-6">
        {renderContent()}
      </div>

      {/* Footer */}
      <div className="bg-gray-100 p-4 text-center text-xs text-gray-600 border-t">
        <p>CONFIDENTIAL - FOR WESTERN FORMULA RACING DAQ TEAM USE ONLY</p>
        <p>Document Rev 2025.1 | Last Updated: August 2025 | Contact: DAQ Team Lead</p>
      </div>
    </div>
  );
};

export default QRHChecklist;