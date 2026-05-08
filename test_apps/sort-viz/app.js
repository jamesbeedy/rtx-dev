// Global variables
let array = [];
let highlights = new Map();
let arraySize = 100;
let speed = 50;
let isSorting = false;
let startTime = 0;
let comparisons = 0;
let swaps = 0;

// DOM Elements
const canvas = document.getElementById('bar-canvas');
const ctx = canvas.getContext('2d');
const arraySizeSlider = document.getElementById('array-size');
const speedSlider = document.getElementById('speed-slider');
const generateBtn = document.getElementById('generate-btn');
const sortBtn = document.getElementById('sort-btn');
const sizeValue = document.getElementById('size-value');
const speedValue = document.getElementById('speed-value');
const comparisonsCount = document.getElementById('comparisons-count');
const swapsCount = document.getElementById('swaps-count');
const timeCount = document.getElementById('time-count');
const algorithmDescription = document.getElementById('algorithm-description');
const algorithmSelect = document.getElementById('algorithm-select');

// Set canvas dimensions
function resizeCanvas() {
    const container = canvas.parentElement;
    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;
    draw();
}

// Initialize
window.addEventListener('load', () => {
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);
    generateArray();
    updateUI();
});

// Event Listeners
arraySizeSlider.addEventListener('input', () => {
    arraySize = parseInt(arraySizeSlider.value);
    sizeValue.textContent = arraySize;
    generateArray();
});

speedSlider.addEventListener('input', () => {
    speed = parseInt(speedSlider.value);
    speedValue.textContent = speed;
});

generateBtn.addEventListener('click', generateArray);
sortBtn.addEventListener('click', toggleSort);

algorithmSelect.addEventListener('change', () => {
    updateAlgorithmDescription();
});

// Update algorithm description
function updateAlgorithmDescription() {
    const descriptions = {
        'bubble': 'Bubble sort repeatedly steps through the list, compares adjacent elements and swaps them if they are in the wrong order.',
        'insertion': 'Insertion sort builds the final sorted array one item at a time by repeatedly taking an element and inserting it into its correct position.',
        'selection': 'Selection sort divides the input into a sorted and unsorted region, repeatedly selecting the smallest element from the unsorted region.',
        'merge': 'Merge sort divides the array into halves, recursively sorts them, and then merges the two sorted halves.',
        'quick': 'Quick sort picks an element as a pivot and partitions the array around the pivot, then recursively sorts the sub-arrays.',
        'heap': 'Heap sort builds a heap from the input data, then repeatedly extracts the maximum element and rebuilds the heap.'
    };
    
    const selected = algorithmSelect.value;
    algorithmDescription.textContent = descriptions[selected];
}

// Generate a new random array
function generateArray() {
    array = [];
    for (let i = 0; i < arraySize; i++) {
        array.push(Math.floor(Math.random() * 950) + 50); // Values between 50-1000
    }
    resetStats();
    draw();
}

// Reset statistics
function resetStats() {
    comparisons = 0;
    swaps = 0;
    startTime = Date.now();
    updateStats();
}

// Update stats display
function updateStats() {
    comparisonsCount.textContent = comparisons;
    swapsCount.textContent = swaps;
    timeCount.textContent = Date.now() - startTime;
}

// Set highlight for a specific index
function setHighlight(index, role) {
    highlights.set(index, role);
}

// Clear all highlights
function clearHighlights() {
    highlights.clear();
}

// Draw the array as bars
function draw() {
    if (!ctx || !canvas) return;
    
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    const barWidth = canvas.width / array.length;
    const maxValue = Math.max(...array);
    
    // Define color mapping
    const colors = {
        'comparing': '#ffd700', // Yellow
        'swapping': '#ff4500',  // Red/orange
        'pivot': '#9370db',     // Purple
        'sorted': '#32cd32',    // Green
        'default': '#555'       // Gray
    };
    
    for (let i = 0; i < array.length; i++) {
        const barHeight = (array[i] / maxValue) * (canvas.height - 40);
        const x = i * barWidth;
        const y = canvas.height - barHeight;
        
        // Determine color based on highlight state
        let color = colors.default;
        if (highlights.has(i)) {
            color = colors[highlights.get(i)] || colors.default;
        }
        
        ctx.fillStyle = color;
        ctx.fillRect(x, y, barWidth - 1, barHeight);
    }
    
    // Clear highlights after drawing to start fresh for next step
    clearHighlights();
}

// Delay function for animation control
async function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// Step function that updates stats and draws with delay
async function step() {
    updateStats();
    draw();
    await delay(speed);
}

// Update UI based on current state
function updateUI() {
    if (isSorting) {
        sortBtn.textContent = 'Stop';
    } else {
        sortBtn.textContent = 'Sort';
    }
}

// Toggle sorting
async function toggleSort() {
    if (isSorting) {
        // Stop sorting
        isSorting = false;
        updateUI();
        return;
    }
    
    // Start sorting
    isSorting = true;
    updateUI();
    await runSortingAlgorithm();
    isSorting = false;
    updateUI();
}

// Run the selected sorting algorithm
async function runSortingAlgorithm() {
    const algorithm = algorithmSelect.value;
    resetStats();
    
    // Validate that we're still sorting
    if (!isSorting) return;
    
    switch (algorithm) {
        case 'bubble':
            await bubbleSort();
            break;
        case 'insertion':
            await insertionSort();
            break;
        case 'selection':
            await selectionSort();
            break;
        case 'merge':
            await mergeSort();
            break;
        case 'quick':
            await quickSort();
            break;
        case 'heap':
            await heapSort();
            break;
        default:
            console.error('Unknown algorithm:', algorithm);
    }
    
    // Verify the sort completed correctly
    if (!isSorted(array)) {
        console.error('Sort failed: ' + algorithm);
    }
    
    // Animate final sweep
    await animateFinalSweep();
}

// Helper to check if array is sorted
function isSorted(arr) {
    for (let i = 0; i < arr.length - 1; i++) {
        if (arr[i] > arr[i + 1]) {
            return false;
        }
    }
    return true;
}

// Animation for final sweep
async function animateFinalSweep() {
    // Create a copy of the original array to preserve values
    const originalArray = [...array];
    
    // Mark all bars as sorted with a sweep effect
    for (let i = 0; i < array.length; i++) {
        if (!isSorting) return;
        setHighlight(i, 'sorted');
        await step();
    }
    
    // Restore original array values (they were preserved)
    array = [...originalArray];
    draw();
}

// Sorting Algorithms Implementation

// Bubble Sort
async function bubbleSort() {
    let n = array.length;
    let swapped = true;
    
    while (swapped) {
        swapped = false;
        for (let i = 0; i < n - 1; i++) {
            if (!isSorting) return;
            
            comparisons++;
            setHighlight(i, 'comparing');
            setHighlight(i + 1, 'comparing');
            await step();
            
            if (array[i] > array[i + 1]) {
                // Swap elements
                [array[i], array[i + 1]] = [array[i + 1], array[i]];
                swaps++;
                swapped = true;

                setHighlight(i, 'swapping');
                setHighlight(i + 1, 'swapping');
                await step();
            }
            
            // Reset highlights
            setHighlight(i, 'default');
            setHighlight(i + 1, 'default');
            await step();
        }
        n--;
    }
}

// Insertion Sort
async function insertionSort() {
    for (let i = 1; i < array.length; i++) {
        if (!isSorting) return;
        
        let key = array[i];
        let j = i - 1;
        
        // Highlight the key being inserted
        setHighlight(i, 'comparing');
        await step();
        
        while (j >= 0 && array[j] > key) {
            if (!isSorting) return;
            
            comparisons++;
            array[j + 1] = array[j]; // Shift element
            swaps++;
            
            // Highlight as swapping
            setHighlight(j + 1, 'swapping');
            await step();
            
            // Reset highlight
            setHighlight(j + 1, 'default');
            await step();
            
            j--;
        }
        
        array[j + 1] = key;
        // Highlight as sorted
        setHighlight(j + 1, 'sorted');
        await step();
    }
}

// Selection Sort
async function selectionSort() {
    for (let i = 0; i < array.length - 1; i++) {
        if (!isSorting) return;
        
        let minIndex = i;
        setHighlight(i, 'comparing');
        await step();
        
        for (let j = i + 1; j < array.length; j++) {
            if (!isSorting) return;
            
            comparisons++;
            setHighlight(j, 'comparing');
            await step();
            
            if (array[j] < array[minIndex]) {
                minIndex = j;
            }
            
            // Reset comparison highlight
            setHighlight(j, 'default');
            await step();
        }
        
        // Swap elements
        if (minIndex !== i) {
            if (!isSorting) return;
            
            [array[i], array[minIndex]] = [array[minIndex], array[i]];
            swaps++;
            
            // Highlight as swapping
            setHighlight(i, 'swapping');
            setHighlight(minIndex, 'swapping');
            await step();
            
            // Reset highlights
            setHighlight(i, 'default');
            setHighlight(minIndex, 'default');
            await step();
        }
        
        // Mark as sorted
        setHighlight(i, 'sorted');
        await step();
    }
    
    // Mark last element as sorted
    setHighlight(array.length - 1, 'sorted');
    await step();
}

// Merge Sort
async function mergeSort() {
    // Operate directly on the global array so bars animate during the sort.
    const arr = array;

    // Helper function for merge sort
    async function mergeSortHelper(arr, left, right) {
        if (left >= right) return;
        
        if (!isSorting) return;
        
        const mid = Math.floor((left + right) / 2);
        await mergeSortHelper(arr, left, mid);
        await mergeSortHelper(arr, mid + 1, right);
        await merge(arr, left, mid, right);
    }
    
    async function merge(arr, left, mid, right) {
        if (!isSorting) return;
        
        const leftArr = arr.slice(left, mid + 1);
        const rightArr = arr.slice(mid + 1, right + 1);
        
        let i = 0, j = 0, k = left;
        
        // Highlight the arrays being merged
        for (let idx = left; idx <= right; idx++) {
            setHighlight(idx, 'comparing');
        }
        await step();
        
        while (i < leftArr.length && j < rightArr.length) {
            if (!isSorting) return;
            
            comparisons++;
            if (leftArr[i] <= rightArr[j]) {
                arr[k] = leftArr[i];
                i++;
            } else {
                arr[k] = rightArr[j];
                j++;
            }
            swaps++;
            
            // Highlight as swapping
            setHighlight(k, 'swapping');
            await step();
            
            // Reset highlight
            setHighlight(k, 'default');
            await step();
            
            k++;
        }
        
        // Copy remaining elements
        while (i < leftArr.length) {
            if (!isSorting) return;
            arr[k] = leftArr[i];
            swaps++;
            setHighlight(k, 'swapping');
            await step();
            setHighlight(k, 'default');
            await step();
            i++;
            k++;
        }
        
        while (j < rightArr.length) {
            if (!isSorting) return;
            arr[k] = rightArr[j];
            swaps++;
            setHighlight(k, 'swapping');
            await step();
            setHighlight(k, 'default');
            await step();
            j++;
            k++;
        }
        
        // Mark as sorted
        for (let idx = left; idx <= right; idx++) {
            setHighlight(idx, 'sorted');
        }
        await step();
    }
    
    await mergeSortHelper(arr, 0, arr.length - 1);
}

// Quick Sort
async function quickSort() {
    const arr = array;

    async function quickSortHelper(arr, low, high) {
        if (low >= high) return;
        
        if (!isSorting) return;
        
        // Highlight pivot
        setHighlight(high, 'pivot');
        await step();
        
        const pi = await partition(arr, low, high);
        
        // Reset pivot highlight
        setHighlight(high, 'default');
        await step();
        
        await quickSortHelper(arr, low, pi - 1);
        await quickSortHelper(arr, pi + 1, high);
    }
    
    async function partition(arr, low, high) {
        const pivot = arr[high];
        let i = low - 1;
        
        for (let j = low; j < high; j++) {
            if (!isSorting) return;
            
            comparisons++;
            setHighlight(j, 'comparing');
            setHighlight(high, 'pivot');
            await step();
            
            if (arr[j] < pivot) {
                i++;
                if (i !== j) {
                    [arr[i], arr[j]] = [arr[j], arr[i]];
                    swaps++;
                    
                    // Highlight as swapping
                    setHighlight(i, 'swapping');
                    setHighlight(j, 'swapping');
                    await step();
                    
                    // Reset highlights
                    setHighlight(i, 'default');
                    setHighlight(j, 'default');
                    await step();
                }
            }
            
            // Reset highlights
            setHighlight(j, 'default');
            setHighlight(high, 'pivot');
            await step();
        }
        
        // Swap pivot to correct position
        if (i + 1 !== high) {
            [arr[i + 1], arr[high]] = [arr[high], arr[i + 1]];
            swaps++;
            
            // Highlight as swapping
            setHighlight(i + 1, 'swapping');
            setHighlight(high, 'swapping');
            await step();
            
            // Reset highlights
            setHighlight(i + 1, 'default');
            setHighlight(high, 'default');
            await step();
        }
        
        return i + 1;
    }
    
    await quickSortHelper(arr, 0, arr.length - 1);
}

// Heap Sort
async function heapSort() {
    const arr = array;

    async function heapify(arr, n, i) {
        let largest = i;
        let left = 2 * i + 1;
        let right = 2 * i + 2;
        
        if (!isSorting) return;
        
        // Highlight nodes being compared
        setHighlight(i, 'comparing');
        if (left < n) setHighlight(left, 'comparing');
        if (right < n) setHighlight(right, 'comparing');
        await step();
        
        if (left < n && arr[left] > arr[largest]) {
            largest = left;
        }
        
        if (right < n && arr[right] > arr[largest]) {
            largest = right;
        }
        
        if (largest !== i) {
            [arr[i], arr[largest]] = [arr[largest], arr[i]];
            swaps++;
            
            // Highlight as swapping
            setHighlight(i, 'swapping');
            setHighlight(largest, 'swapping');
            await step();
            
            // Reset highlights
            setHighlight(i, 'default');
            setHighlight(largest, 'default');
            await step();
            
            await heapify(arr, n, largest);
        }
        
        // Reset highlights
        setHighlight(i, 'default');
        if (left < n) setHighlight(left, 'default');
        if (right < n) setHighlight(right, 'default');
        await step();
    }
    
    // Build max heap
    for (let i = Math.floor(arr.length / 2) - 1; i >= 0; i--) {
        if (!isSorting) return;
        await heapify(arr, arr.length, i);
    }
    
    // Extract elements from heap
    for (let i = arr.length - 1; i > 0; i--) {
        if (!isSorting) return;
        
        // Move current root to end
        [arr[0], arr[i]] = [arr[i], arr[0]];
        swaps++;
        
        // Highlight as swapping
        setHighlight(0, 'swapping');
        setHighlight(i, 'swapping');
        await step();
        
        // Reset highlights
        setHighlight(0, 'default');
        setHighlight(i, 'default');
        await step();
        
        // Call heapify on the reduced heap
        await heapify(arr, i, 0);
    }
}

// Initialize the algorithm description
updateAlgorithmDescription();