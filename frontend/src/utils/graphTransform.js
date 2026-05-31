/**
 * Transform API response to ReactFlow-compatible graph format.
 * Supports both old Excel relationship format and new ThoughtSpot format:
 *
 * Old:
 * rel.source.file, rel.source.column, rel.target.file, rel.target.column
 *
 * ThoughtSpot:
 * rel.source_table, rel.source_column, rel.target_table, rel.target_column
 */

import { extractOriginalFilename } from './fileUtils.js';

function normalizeName(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/\.[^.]+$/, '')
    .replace(/[^a-z0-9]/g, '');
}

function getTableName(file) {
  return (
    file?.table_name ||
    file?.display_name ||
    file?.sheet_name ||
    file?.file_name ||
    file?.name ||
    'Unknown Table'
  );
}

function getColumnName(column) {
  return (
    column?.column_name ||
    column?.name ||
    column?.display_name ||
    column?.column ||
    'Unknown Column'
  );
}

function getRelationshipSourceTable(rel) {
  return (
    rel?.source_table ||
    rel?.from_table ||
    rel?.source?.table ||
    rel?.source?.file ||
    rel?.sourceFile ||
    rel?.source_file ||
    ''
  );
}

function getRelationshipTargetTable(rel) {
  return (
    rel?.target_table ||
    rel?.to_table ||
    rel?.target?.table ||
    rel?.target?.file ||
    rel?.targetFile ||
    rel?.target_file ||
    ''
  );
}

function getRelationshipSourceColumn(rel) {
  return (
    rel?.source_column ||
    rel?.from_column ||
    rel?.source_key ||
    rel?.source?.column ||
    rel?.sourceColumn ||
    ''
  );
}

function getRelationshipTargetColumn(rel) {
  return (
    rel?.target_column ||
    rel?.to_column ||
    rel?.target_key ||
    rel?.target?.column ||
    rel?.targetColumn ||
    ''
  );
}

function findFileIndex(files, tableName) {
  const normalizedTableName = normalizeName(tableName);

  return files.findIndex((file) => {
    const candidates = [
      file?.table_name,
      file?.display_name,
      file?.sheet_name,
      file?.file_name,
      file?.name,
      file?.file_path,
    ];

    return candidates.some(
      (candidate) => normalizeName(candidate) === normalizedTableName
    );
  });
}

function findColumnIndex(file, columnName) {
  const normalizedColumnName = normalizeName(columnName);
  const columns = Array.isArray(file?.columns) ? file.columns : [];

  return columns.findIndex((column) => {
    const candidate = getColumnName(column);
    return normalizeName(candidate) === normalizedColumnName;
  });
}

function getEdgeColor(isIncluded = true) {
  return isIncluded ? '#2563eb' : '#d1d5db';
}

function getEdgeStyle(isIncluded = true) {
  if (!isIncluded) {
    return {
      stroke: '#d1d5db',
      strokeWidth: 2,
      strokeDasharray: '5,5',
      opacity: 0.5,
    };
  }

  return {
    stroke: '#2563eb',
    strokeWidth: 3,
    opacity: 1,
  };
}

function normalizeFiles(rawFiles = []) {
  const seen = new Map();

  rawFiles.forEach((file) => {
    const tableName = getTableName(file);
    const key = normalizeName(tableName);

    if (!key) return;

    const columns = Array.isArray(file?.columns) ? file.columns : [];

    if (!seen.has(key)) {
      seen.set(key, {
        ...file,
        file_name: tableName,
        sheet_name: tableName,
        table_name: tableName,
        columns: [...columns],
      });
      return;
    }

    const existing = seen.get(key);
    const existingColumnKeys = new Set(
      (existing.columns || []).map((column) =>
        normalizeName(getColumnName(column))
      )
    );

    columns.forEach((column) => {
      const columnKey = normalizeName(getColumnName(column));

      if (columnKey && !existingColumnKeys.has(columnKey)) {
        existing.columns.push(column);
        existingColumnKeys.add(columnKey);
      }
    });

    existing.row_count = Math.max(existing.row_count || 0, file?.row_count || 0);
    existing.column_count = existing.columns.length;
  });

  return Array.from(seen.values());
}

export const transformToReactFlow = (apiResult, relationshipInclusion = {}) => {
  const nodes = [];
  const edges = [];

  if (!apiResult || !apiResult.result || !Array.isArray(apiResult.result.files)) {
    return { nodes, edges };
  }

  const files = normalizeFiles(apiResult.result.files);

  const relationships = Array.isArray(apiResult.result.relationships)
    ? apiResult.result.relationships
    : [];

  const activeRelationships = relationships.filter((relationship) => {
    return relationship && !relationship.deleted;
  });

  files.forEach((file, fileIndex) => {
    const tableName = getTableName(file);
    const columns = Array.isArray(file.columns) ? file.columns : [];

    const tableHeight = Math.max(120, 80 + columns.length * 32);

    nodes.push({
      id: `file-${fileIndex}`,
      type: 'fileNode',
      data: {
        label: extractOriginalFilename(tableName),
        sheet: tableName,
        rowCount: file.row_count || 0,
        columnCount: columns.length,
        columns,
      },
      position: {
        x: fileIndex * 520,
        y: 0,
      },
      style: {
        width: 320,
        height: tableHeight,
        backgroundColor: '#f8fafc',
        border: '2px solid #2563eb',
        borderRadius: '10px',
        padding: '16px',
      },
    });

    columns.forEach((column, columnIndex) => {
      const columnName = getColumnName(column);
      const dataType =
        column?.data_type || column?.datatype || column?.type || 'unknown';

      const normalizedColumnName = normalizeName(columnName);

      const isPrimaryKey =
        column?.is_primary_key ||
        normalizedColumnName === 'id' ||
        normalizedColumnName.endsWith('id');

      nodes.push({
        id: `file-${fileIndex}-col-${columnIndex}`,
        type: 'columnNode',
        data: {
          label: columnName,
          dataType,
          isPrimaryKey,
          isForeignKey: column?.is_foreign_key || false,
          fileIndex,
          colIndex: columnIndex,
          tableName,
        },
        parentNode: `file-${fileIndex}`,
        extent: 'parent',
        position: {
          x: 12,
          y: 70 + columnIndex * 32,
        },
        style: {
          width: 285,
          height: 26,
          backgroundColor: isPrimaryKey ? '#dbeafe' : '#e0e7ff',
          border: '1px solid #93c5fd',
          borderRadius: '6px',
        },
      });
    });
  });

  const seenEdges = new Set();

  activeRelationships.forEach((rel, relIndex) => {
    const sourceTable = getRelationshipSourceTable(rel);
    const targetTable = getRelationshipTargetTable(rel);
    const sourceColumn = getRelationshipSourceColumn(rel);
    const targetColumn = getRelationshipTargetColumn(rel);

    if (!sourceTable || !targetTable || !sourceColumn || !targetColumn) {
      console.warn('Invalid relationship skipped:', rel);
      return;
    }

    if (normalizeName(sourceTable) === normalizeName(targetTable)) {
      console.warn('Self relationship skipped:', rel);
      return;
    }

    const sourceFileIndex = findFileIndex(files, sourceTable);
    const targetFileIndex = findFileIndex(files, targetTable);

    if (sourceFileIndex === -1 || targetFileIndex === -1) {
      console.warn('Table not found for relationship:', {
        sourceTable,
        targetTable,
        availableTables: files.map(getTableName),
        relationship: rel,
      });
      return;
    }

    const sourceFile = files[sourceFileIndex];
    const targetFile = files[targetFileIndex];

    const sourceColumnIndex = findColumnIndex(sourceFile, sourceColumn);
    const targetColumnIndex = findColumnIndex(targetFile, targetColumn);

    if (sourceColumnIndex === -1 || targetColumnIndex === -1) {
      console.warn('Column not found for relationship:', {
        sourceTable,
        sourceColumn,
        targetTable,
        targetColumn,
        sourceColumns: sourceFile.columns?.map(getColumnName),
        targetColumns: targetFile.columns?.map(getColumnName),
        relationship: rel,
      });
      return;
    }

    const edgeKey = [
      normalizeName(sourceTable),
      normalizeName(sourceColumn),
      normalizeName(targetTable),
      normalizeName(targetColumn),
    ].join('__');

    if (seenEdges.has(edgeKey)) {
      return;
    }

    seenEdges.add(edgeKey);

    const relationshipId =
      rel.relationship_id ||
      rel.id ||
      `relationship-${sourceTable}-${sourceColumn}-${targetTable}-${targetColumn}`;

    const isIncluded =
      relationshipInclusion?.[relationshipId]?.included !== false;

    edges.push({
      id: `edge-${relIndex}-${edgeKey}`,
      source: `file-${sourceFileIndex}-col-${sourceColumnIndex}`,
      target: `file-${targetFileIndex}-col-${targetColumnIndex}`,
      type: 'relationship',
      animated: false,
      interactionWidth: 30,
      label: `${sourceColumn} → ${targetColumn}`,
      data: {
        relationship: rel,
        sourceTable,
        targetTable,
        sourceColumn,
        targetColumn,
        source_column: sourceColumn,
        target_column: targetColumn,
        confidenceLevel: rel.confidence_level || rel.confidence || 'HIGH',
        isIncluded,
      },
      style: getEdgeStyle(isIncluded),
      markerEnd: {
        type: 'arrowclosed',
        color: getEdgeColor(isIncluded),
      },
      labelStyle: {
        fill: '#111827',
        fontWeight: 600,
        fontSize: 12,
      },
      labelBgStyle: {
        fill: '#ffffff',
        fillOpacity: 0.95,
      },
    });
  });

  return { nodes, edges };
};

export const countByConfidence = (edges) => {
  return edges.reduce(
    (acc, edge) => {
      const level = edge.data?.confidenceLevel || 'MEDIUM';
      acc[level] = (acc[level] || 0) + 1;
      return acc;
    },
    { HIGH: 0, MEDIUM: 0, LOW: 0 }
  );
};