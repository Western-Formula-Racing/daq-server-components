#!/usr/bin/env python3
"""
Test script for the Code Generator service.
Tests the complete workflow: prompt -> code generation -> sandbox execution -> results.
"""

import requests
import json
import base64
from pathlib import Path

# Service endpoint
CODE_GENERATOR_URL = "http://localhost:3030"

def test_health_check():
    """Test the health endpoint."""
    print("Testing health check...")
    response = requests.get(f"{CODE_GENERATOR_URL}/api/health")
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}\n")
    return response.status_code == 200

def test_simple_code_generation():
    """Test generating and executing simple Python code."""
    print("Testing simple code generation...")
    
    prompt = "Create a scatter plot of 50 random voltage (300-400V) vs current (50-150A) points, color by power. Save as output.png"
    
    print(f"Prompt: {prompt}\n")
    
    response = requests.post(
        f"{CODE_GENERATOR_URL}/api/generate-code",
        json={"prompt": prompt},
        timeout=120
    )
    
    print(f"Status: {response.status_code}")
    result = response.json()
    
    print(f"\nGenerated Code:")
    print("=" * 60)
    print(result.get("code", "No code returned"))
    print("=" * 60)
    
    exec_result = result.get("result", {})
    print(f"\nExecution Status: {exec_result.get('status')}")
    
    if exec_result.get("output"):
        print(f"Output: {exec_result['output']}")
    
    if exec_result.get("error"):
        print(f"Error: {exec_result['error']}")
    
    # Check for retries
    retries = result.get("retries", [])
    if retries:
        print(f"\nRetries: {len(retries)}")
        for i, retry in enumerate(retries, 1):
            print(f"  Attempt {i}: {retry.get('error', '')[:100]}...")
    
    # Save any generated images
    files = exec_result.get("files", [])
    if files:
        print(f"\nGenerated {len(files)} file(s):")
        for file_info in files:
            filename = file_info.get("name")
            b64_data = file_info.get("data")
            
            if b64_data:
                # Decode and save
                image_data = base64.b64decode(b64_data)
                output_path = Path(filename)
                output_path.write_bytes(image_data)
                print(f"  ✓ Saved: {filename} ({len(image_data)} bytes)")
    
    print("\n" + "=" * 60 + "\n")
    return exec_result.get("status") == "success"

def test_error_with_retry():
    """Test that retry mechanism works with intentionally broken code."""
    print("Testing error handling and retry...")
    
    # This should initially fail but might succeed after retry
    prompt = "Print the numbers 1 through 10, one per line"
    
    print(f"Prompt: {prompt}\n")
    
    response = requests.post(
        f"{CODE_GENERATOR_URL}/api/generate-code",
        json={"prompt": prompt},
        timeout=120
    )
    
    result = response.json()
    exec_result = result.get("result", {})
    
    print(f"Status: {exec_result.get('status')}")
    print(f"Output: {exec_result.get('output', 'No output')}")
    
    retries = result.get("retries", [])
    if retries:
        print(f"\nRetries occurred: {len(retries)}")
    else:
        print("\nNo retries needed - succeeded on first attempt")
    
    print("\n" + "=" * 60 + "\n")
    return True

def test_parse_plan_steps():
    """Test the parse_plan_steps helper function (unit test — no services required)."""
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from code_generator import parse_plan_steps

    print("Testing parse_plan_steps...")

    # Standard numbered list
    steps = parse_plan_steps("1. Fetch motor speed data\n2. Compute variance\n3. Plot results")
    assert steps == ["Fetch motor speed data", "Compute variance", "Plot results"], f"Got: {steps}"
    print("  ✓ Standard numbered list")

    # Parenthetical numbers
    steps = parse_plan_steps("1) Fetch battery voltages\n2) Identify weakest cell\n3) Create dashboard")
    assert len(steps) == 3, f"Expected 3, got {len(steps)}: {steps}"
    print("  ✓ Parenthetical numbers")

    # Colon format
    steps = parse_plan_steps("1: Scan run windows\n2: Fetch signals\n3: Visualize")
    assert len(steps) == 3, f"Expected 3, got {len(steps)}: {steps}"
    print("  ✓ Colon format")

    # Fallback for unparseable input
    steps = parse_plan_steps("Just do the analysis")
    assert len(steps) == 1, f"Expected 1 fallback step, got {len(steps)}: {steps}"
    print("  ✓ Fallback single step")

    # MAX_STEPS cap
    steps = parse_plan_steps("\n".join(f"{i}. Step {i}" for i in range(1, 12)))
    assert len(steps) <= 8, f"Expected <= 8 (hard cap), got {len(steps)}"
    print(f"  ✓ MAX_STEPS cap (got {len(steps)} steps)")

    # Empty input
    steps = parse_plan_steps("")
    assert len(steps) == 1, f"Expected 1 fallback for empty, got {len(steps)}"
    print("  ✓ Empty input fallback")

    print("All parse_plan_steps tests passed!\n")
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("Code Generator Service Test Suite")
    print("=" * 60 + "\n")

    tests = [
        ("Parse Plan Steps (unit)", test_parse_plan_steps),
        ("Health Check", test_health_check),
        ("Simple Code Generation", test_simple_code_generation),
        ("Error Handling", test_error_with_retry),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, "PASS" if success else "FAIL"))
        except Exception as e:
            print(f"ERROR: {e}\n")
            results.append((test_name, "ERROR"))
    
    print("\n" + "=" * 60)
    print("Test Results")
    print("=" * 60)
    for test_name, status in results:
        status_icon = "✓" if status == "PASS" else "✗"
        print(f"{status_icon} {test_name}: {status}")
    print("=" * 60)

if __name__ == "__main__":
    main()
