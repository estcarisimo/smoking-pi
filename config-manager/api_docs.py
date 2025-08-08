#!/usr/bin/env python3
"""
OpenAPI/Swagger documentation for Config Manager REST API
"""

from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# OpenAPI 3.0 specification
OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {
        "title": "SmokePing Config Manager API",
        "description": "REST API for managing SmokePing configuration",
        "version": "1.0.0",
        "contact": {
            "name": "SmokePing Config Manager",
            "url": "https://github.com/smokingpi/smoking-pi"
        }
    },
    "servers": [
        {
            "url": "http://localhost:5000",
            "description": "Local development server"
        },
        {
            "url": "http://config-manager:5000",
            "description": "Docker container server"
        }
    ],
    "paths": {
        "/health": {
            "get": {
                "summary": "Health check",
                "description": "Check if the config manager service is healthy",
                "responses": {
                    "200": {
                        "description": "Service is healthy",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "status": {"type": "string", "example": "healthy"},
                                        "service": {"type": "string", "example": "config-manager"},
                                        "timestamp": {"type": "string", "format": "date-time"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "/status": {
            "get": {
                "summary": "Get service status",
                "description": "Get comprehensive status of config manager and SmokePing services",
                "responses": {
                    "200": {
                        "description": "Service status information",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ServiceStatus"
                                }
                            }
                        }
                    },
                    "500": {
                        "description": "Status check failed",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Error"
                                }
                            }
                        }
                    }
                }
            }
        },
        "/config": {
            "get": {
                "summary": "Get all configurations",
                "description": "Retrieve all configuration files (targets, probes, sources)",
                "responses": {
                    "200": {
                        "description": "Configuration data",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/AllConfigurations"
                                }
                            }
                        }
                    },
                    "400": {
                        "description": "Invalid request",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Error"
                                }
                            }
                        }
                    },
                    "500": {
                        "description": "Server error",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Error"
                                }
                            }
                        }
                    }
                }
            }
        },
        "/config/{config_type}": {
            "get": {
                "summary": "Get specific configuration",
                "description": "Retrieve a specific configuration file",
                "parameters": [
                    {
                        "name": "config_type",
                        "in": "path",
                        "required": True,
                        "schema": {
                            "type": "string",
                            "enum": ["targets", "probes", "sources"]
                        },
                        "description": "Type of configuration to retrieve"
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Configuration data",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Configuration"
                                }
                            }
                        }
                    },
                    "400": {
                        "description": "Invalid config type",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Error"
                                }
                            }
                        }
                    },
                    "500": {
                        "description": "Server error",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Error"
                                }
                            }
                        }
                    }
                }
            },
            "put": {
                "summary": "Update configuration",
                "description": "Update a specific configuration file",
                "parameters": [
                    {
                        "name": "config_type",
                        "in": "path",
                        "required": True,
                        "schema": {
                            "type": "string",
                            "enum": ["targets", "probes", "sources"]
                        },
                        "description": "Type of configuration to update"
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "description": "Configuration data to update"
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "Configuration updated successfully",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Success"
                                }
                            }
                        }
                    },
                    "400": {
                        "description": "Invalid configuration data",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Error"
                                }
                            }
                        }
                    },
                    "500": {
                        "description": "Update failed",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Error"
                                }
                            }
                        }
                    }
                }
            }
        },
        "/generate": {
            "post": {
                "summary": "Generate SmokePing configuration",
                "description": "Generate and deploy SmokePing configuration files from YAML sources",
                "responses": {
                    "200": {
                        "description": "Configuration generated successfully",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Success"
                                }
                            }
                        }
                    },
                    "500": {
                        "description": "Generation failed",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Error"
                                }
                            }
                        }
                    }
                }
            }
        },
        "/restart": {
            "post": {
                "summary": "Restart SmokePing service",
                "description": "Restart the SmokePing Docker container",
                "responses": {
                    "200": {
                        "description": "Service restarted successfully",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Success"
                                }
                            }
                        }
                    },
                    "500": {
                        "description": "Restart failed",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Error"
                                }
                            }
                        }
                    }
                }
            }
        },
        "/oca/refresh": {
            "post": {
                "summary": "Refresh OCA data",
                "description": "Refresh Netflix Open Connect Appliance server data",
                "responses": {
                    "200": {
                        "description": "OCA data refreshed successfully",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "success": {"type": "boolean"},
                                        "message": {"type": "string"},
                                        "output": {"type": "string"},
                                        "refreshed_at": {"type": "string", "format": "date-time"}
                                    }
                                }
                            }
                        }
                    },
                    "500": {
                        "description": "OCA refresh failed",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Error"
                                }
                            }
                        }
                    }
                }
            }
        }
    },
    "components": {
        "schemas": {
            "Error": {
                "type": "object",
                "properties": {
                    "error": {
                        "type": "string",
                        "description": "Error message"
                    },
                    "timestamp": {
                        "type": "string",
                        "format": "date-time",
                        "description": "Error timestamp"
                    }
                },
                "required": ["error"]
            },
            "Success": {
                "type": "object",
                "properties": {
                    "success": {
                        "type": "boolean",
                        "description": "Operation success status"
                    },
                    "message": {
                        "type": "string",
                        "description": "Success message"
                    },
                    "generated_at": {
                        "type": "string",
                        "format": "date-time",
                        "description": "Operation timestamp"
                    }
                },
                "required": ["success", "message"]
            },
            "ServiceStatus": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["healthy", "partial", "error"],
                        "description": "Overall service status"
                    },
                    "configs": {
                        "type": "object",
                        "properties": {
                            "targets": {"type": "boolean"},
                            "probes": {"type": "boolean"},
                            "sources": {"type": "boolean"}
                        },
                        "description": "Configuration file existence status"
                    },
                    "generated_files": {
                        "type": "object",
                        "properties": {
                            "targets_file": {"type": "boolean"},
                            "probes_file": {"type": "boolean"}
                        },
                        "description": "Generated SmokePing file status"
                    },
                    "smokeping": {
                        "type": "object",
                        "properties": {
                            "running": {"type": "boolean"},
                            "status": {"type": "string"}
                        },
                        "description": "SmokePing container status"
                    },
                    "last_check": {
                        "type": "string",
                        "format": "date-time",
                        "description": "Last status check timestamp"
                    }
                },
                "required": ["status", "last_check"]
            },
            "Configuration": {
                "type": "object",
                "properties": {
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "last_modified": {"type": "string", "format": "date-time"},
                            "generated_at": {"type": "string", "format": "date-time"},
                            "config_type": {"type": "string"}
                        },
                        "description": "Configuration metadata"
                    }
                },
                "additionalProperties": True,
                "description": "Configuration data with metadata"
            },
            "AllConfigurations": {
                "type": "object",
                "properties": {
                    "targets": {
                        "type": "object",
                        "description": "Targets configuration"
                    },
                    "probes": {
                        "type": "object",
                        "description": "Probes configuration"
                    },
                    "sources": {
                        "type": "object",
                        "description": "Sources configuration"
                    },
                    "metadata": {
                        "$ref": "#/components/schemas/Configuration/properties/metadata"
                    }
                },
                "description": "All configuration files combined"
            }
        }
    }
}


@app.route('/api/docs')
def get_api_docs():
    """Get OpenAPI specification"""
    return jsonify(OPENAPI_SPEC)


@app.route('/')
def swagger_ui():
    """Swagger UI for API documentation"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>SmokePing Config Manager API Documentation</title>
        <link rel="stylesheet" type="text/css" href="https://unpkg.com/swagger-ui-dist@4.15.5/swagger-ui.css" />
        <style>
            html { box-sizing: border-box; overflow: -moz-scrollbars-vertical; overflow-y: scroll; }
            *, *:before, *:after { box-sizing: inherit; }
            body { margin:0; background: #fafafa; }
        </style>
    </head>
    <body>
        <div id="swagger-ui"></div>
        <script src="https://unpkg.com/swagger-ui-dist@4.15.5/swagger-ui-bundle.js"></script>
        <script src="https://unpkg.com/swagger-ui-dist@4.15.5/swagger-ui-standalone-preset.js"></script>
        <script>
            window.onload = function() {
                const ui = SwaggerUIBundle({
                    url: '/api/docs',
                    dom_id: '#swagger-ui',
                    deepLinking: true,
                    presets: [
                        SwaggerUIBundle.presets.apis,
                        SwaggerUIStandalonePreset
                    ],
                    plugins: [
                        SwaggerUIBundle.plugins.DownloadUrl
                    ],
                    layout: "StandaloneLayout"
                });
            };
        </script>
    </body>
    </html>
    """


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)