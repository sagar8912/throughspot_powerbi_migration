import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, Loader, Database, GitBranch } from 'lucide-react';
import toast from 'react-hot-toast';

import Card from '../../components/common/Card';
import Button from '../../components/common/Button';
import useMigrationStore from '../../stores/migrationStore';
import useMigrationCacheStore from '../../stores/migrationCacheStore';
import migrationApi from '../../services/migrationApi';
import MigrationSidebar from '../../components/migration/MigrationSidebar';

import { useGraphStore } from '../../stores/graphStore';
import { transformToReactFlow } from '../../utils/graphTransform';
import GraphCanvas from '../../components/visualization/GraphCanvas';

const normalizeName = (value = '') =>
  String(value)
    .trim()
    .toLowerCase()
    .replace(/\.[^.]+$/, '')
    .replace(/\s+/g, ' ')
    .replace(/[^a-z0-9_ ]/g, '');

const getTableName = (table) =>
  table?.table_name ||
  table?.display_name ||
  table?.name ||
  table?.sheet_name ||
  table?.file_name ||
  'Unknown Table';

const getColumnName = (column, index) =>
  column?.name ||
  column?.display_name ||
  column?.column_name ||
  column?.field_name ||
  `Column_${index + 1}`;

const getTableColumns = (table) => {
  const rawColumns =
    table?.column_details ||
    table?.columns ||
    table?.fields ||
    [];

  if (!Array.isArray(rawColumns)) {
    return [];
  }

  const seenColumns = new Set();

  return rawColumns
    .map((column, index) => {
      if (typeof column === 'string') {
        return {
          name: column,
          display_name: column,
          data_type: 'unknown',
          datatype: 'unknown',
        };
      }

      const columnName = getColumnName(column, index);
      const key = normalizeName(columnName);

      if (!key || seenColumns.has(key)) {
        return null;
      }

      seenColumns.add(key);

      return {
        name: columnName,
        display_name: column?.display_name || columnName,
        data_type: column?.data_type || column?.datatype || column?.type || 'unknown',
        datatype: column?.datatype || column?.data_type || column?.type || 'unknown',
        role: column?.role || 'attribute',
      };
    })
    .filter(Boolean);
};

/**
 * Merge duplicate tables before sending data to React Flow.
 *
 * Why this is needed:
 * - Backend/source metadata can contain the same table multiple times.
 * - React Flow will draw every repeated table as a separate box.
 * - For final migration output, one physical/source table should appear once.
 */
const dedupeAndMergeTables = (tables = []) => {
  const tableMap = new Map();

  tables.forEach((table, index) => {
    const tableName = getTableName(table);
    const key = normalizeName(tableName) || `table_${index + 1}`;
    const columns = getTableColumns(table);

    if (!tableMap.has(key)) {
      tableMap.set(key, {
        ...table,
        table_name: tableName,
        display_name: table?.display_name || tableName,
        row_count: Number(table?.row_count || 0),
        column_details: [],
        columns: [],
        _columnMap: new Map(),
      });
    }

    const existing = tableMap.get(key);

    existing.row_count = Math.max(
      Number(existing.row_count || 0),
      Number(table?.row_count || 0)
    );

    columns.forEach((column) => {
      const columnKey = normalizeName(column.name);

      if (!existing._columnMap.has(columnKey)) {
        existing._columnMap.set(columnKey, column);
      }
    });
  });

  return Array.from(tableMap.values()).map((table) => {
    const mergedColumns = Array.from(table._columnMap.values());

    const { _columnMap, ...cleanTable } = table;

    return {
      ...cleanTable,
      column_details: mergedColumns,
      columns: mergedColumns,
    };
  });
};

const getRelationshipTable = (relationship, keys) => {
  for (const key of keys) {
    if (relationship?.[key]) {
      return relationship[key];
    }
  }

  return '';
};

const getRelationshipColumn = (relationship, keys) => {
  for (const key of keys) {
    if (relationship?.[key]) {
      return relationship[key];
    }
  }

  return '';
};

/**
 * Keep only real relationships:
 * - both source and target table must exist
 * - source table and target table must be different
 * - duplicate relationship lines are removed
 */
const dedupeRelationships = (relationships = [], validTableNames = new Set()) => {
  const seen = new Set();
  const cleaned = [];

  relationships.forEach((relationship, index) => {
    const sourceTable = getRelationshipTable(relationship, [
      'source_table',
      'from_table',
      'left_table',
      'table',
    ]);

    const targetTable = getRelationshipTable(relationship, [
      'target_table',
      'to_table',
      'right_table',
      'related_table',
    ]);

    const sourceColumn = getRelationshipColumn(relationship, [
      'source_column',
      'from_column',
      'source_key',
      'left_column',
      'column',
    ]);

    const targetColumn = getRelationshipColumn(relationship, [
      'target_column',
      'to_column',
      'target_key',
      'right_column',
      'related_column',
    ]);

    const sourceKey = normalizeName(sourceTable);
    const targetKey = normalizeName(targetTable);
    const sourceColumnKey = normalizeName(sourceColumn);
    const targetColumnKey = normalizeName(targetColumn);

    if (!sourceKey || !targetKey) {
      return;
    }

    if (sourceKey === targetKey) {
      return;
    }

    if (!validTableNames.has(sourceKey) || !validTableNames.has(targetKey)) {
      return;
    }

    const relationshipKey = [
      sourceKey,
      sourceColumnKey,
      targetKey,
      targetColumnKey,
    ].join('|');

    const reverseRelationshipKey = [
      targetKey,
      targetColumnKey,
      sourceKey,
      sourceColumnKey,
    ].join('|');

    if (seen.has(relationshipKey) || seen.has(reverseRelationshipKey)) {
      return;
    }

    seen.add(relationshipKey);

    cleaned.push({
      ...relationship,
      relationship_id:
        relationship?.relationship_id ||
        relationship?.id ||
        `relationship_${index + 1}`,
      source_table: sourceTable,
      target_table: targetTable,
      source_column: sourceColumn,
      target_column: targetColumn,
      relationship_type:
        relationship?.relationship_type ||
        relationship?.cardinality ||
        'many_to_one',
      cardinality:
        relationship?.cardinality ||
        relationship?.relationship_type ||
        'many_to_one',
      confidence_score: relationship?.confidence_score ?? 0.9,
      active: relationship?.active ?? true,
    });
  });

  return cleaned;
};

export default function Page2ModelIntelligence() {
  const navigate = useNavigate();

  const { currentMigration } = useMigrationStore();
  const { loadModelIntelligence } = useMigrationCacheStore();

  const [isLoading, setIsLoading] = useState(true);
  const [tables, setTables] = useState([]);
  const [relationships, setRelationships] = useState([]);

  const loadData = useCallback(async () => {
    if (!currentMigration?.migration_id) {
      return;
    }

    setIsLoading(true);

    try {
      console.log('[Page2] Loading model intelligence and relationships...');

      const migrationId = currentMigration.migration_id;

      const [modelData, relationshipsData] = await Promise.all([
        loadModelIntelligence(migrationId),
        migrationApi.getSuggestedRelationships(migrationId),
      ]);

      const rawTables = Array.isArray(modelData?.tables)
        ? modelData.tables
        : [];

      const uniqueTables = dedupeAndMergeTables(rawTables);
      const validTableNames = new Set(
        uniqueTables.map((table) => normalizeName(getTableName(table)))
      );

      const rawRelationships = Array.isArray(relationshipsData?.relationships)
        ? relationshipsData.relationships
        : Array.isArray(modelData?.relationships)
          ? modelData.relationships
          : [];

      const uniqueRelationships = dedupeRelationships(
        rawRelationships,
        validTableNames
      );

      setTables(uniqueTables);
      setRelationships(uniqueRelationships);

      const filesForGraph = uniqueTables.map((table) => {
        const tableName = getTableName(table);
        const columns = getTableColumns(table);

        return {
          file_name: tableName,
          sheet_name: tableName,
          table_name: tableName,
          row_count: Number(table?.row_count || 0),
          column_count: columns.length,
          columns: columns.map((column) => ({
            column_name: column?.name || 'Unknown Column',
            name: column?.name || 'Unknown Column',
            data_type: column?.data_type || column?.datatype || 'unknown',
          })),
        };
      });

      const apiResult = {
        result: {
          files: filesForGraph,
          tables: uniqueTables,
          relationships: uniqueRelationships,
        },
      };

      try {
        const { nodes: graphNodes, edges: graphEdges } = transformToReactFlow(
          apiResult,
          {}
        );

        useGraphStore.setState({
          nodes: graphNodes || [],
          edges: graphEdges || [],
          filteredEdges: graphEdges || [],
          relationshipInclusion: {},
        });
      } catch (graphError) {
        console.error('Failed to transform graph data:', graphError);

        useGraphStore.setState({
          nodes: [],
          edges: [],
          filteredEdges: [],
          relationshipInclusion: {},
        });

        toast.error('Failed to render relationship graph');
      }
    } catch (error) {
      console.error('Failed to load model intelligence:', error);
      toast.error('Failed to load data model');
    } finally {
      setIsLoading(false);
    }
  }, [currentMigration?.migration_id, loadModelIntelligence]);

  useEffect(() => {
    if (!currentMigration?.migration_id) {
      toast.error('No migration found. Please upload a ThoughtSpot file first.');
      navigate('/migration');
      return;
    }

    loadData();
  }, [currentMigration?.migration_id, navigate, loadData]);

  if (isLoading) {
    return (
      <div
        className="h-screen flex overflow-hidden"
        style={{ backgroundColor: '#e5e5e5' }}
      >
        <MigrationSidebar currentStep={2} />

        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <Loader className="w-8 h-8 animate-spin text-blue-600 mx-auto mb-4" />
            <p className="text-gray-600">Loading data model...</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="h-screen flex overflow-hidden"
      style={{ backgroundColor: '#e5e5e5' }}
    >
      <MigrationSidebar currentStep={2} />

      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="bg-white border-b border-gray-200 shadow-sm px-6 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">
                Data Model Configuration
              </h1>

              <p className="text-sm text-gray-600 mt-1">
                Visualizing unique ThoughtSpot source tables and relationships
              </p>
            </div>

            <div className="flex items-center gap-3">
              <Button
                variant="secondary"
                onClick={() =>
                  navigate('/migration-wizard/data-understanding')
                }
              >
                Back
              </Button>

              <Button onClick={() => navigate('/migration-wizard/field-mapping')}>
                Next Step
              </Button>
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 p-6 flex flex-col min-h-0">
          {tables.length === 0 ? (
            <Card className="text-center py-12">
              <AlertCircle className="w-12 h-12 text-gray-400 mx-auto mb-4" />

              <h3 className="text-lg font-semibold text-gray-900 mb-2">
                No Tables Found
              </h3>

              <p className="text-gray-600">
                No data tables were found in the source file. Please check the
                uploaded ThoughtSpot file.
              </p>
            </Card>
          ) : (
            <div className="flex-1 bg-white rounded-xl border border-gray-200 shadow-lg overflow-hidden flex flex-col min-h-0">
              <div className="px-6 py-4 bg-gray-50 border-b border-gray-200 shrink-0">
                <div className="flex items-center justify-between gap-4">
                  <h2 className="text-lg font-semibold text-gray-900">
                    Relationship Diagram
                  </h2>

                  <div className="flex items-center gap-4 text-sm text-gray-600">
                    <span className="inline-flex items-center gap-1">
                      <Database className="w-4 h-4" />
                      {tables.length} unique table{tables.length === 1 ? '' : 's'}
                    </span>

                    <span className="inline-flex items-center gap-1">
                      <GitBranch className="w-4 h-4" />
                      {relationships.length} relationship{relationships.length === 1 ? '' : 's'}
                    </span>
                  </div>
                </div>

                {relationships.length === 0 && tables.length > 1 && (
                  <p className="text-xs text-amber-700 mt-2">
                    Tables were found, but no valid relationship keys were detected.
                    Check source metadata joins or matching ID columns.
                  </p>
                )}
              </div>

              <div className="flex-1 relative">
                <GraphCanvas />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
