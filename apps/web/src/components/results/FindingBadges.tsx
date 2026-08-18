import React from 'react';
import { EmailClassification, EmailValidationStatus } from '@/types/api';

interface ClassificationBadgeProps {
  classification: EmailClassification | string;
  className?: string;
}

export const ClassificationBadge: React.FC<ClassificationBadgeProps> = ({
  classification,
  className = '',
}) => {
  let label = classification as string;
  let styleClasses = 'bg-slate-100 text-slate-700 border-slate-200';

  switch (classification) {
    case 'ROLE_BASED':
      label = 'Role address';
      styleClasses = 'bg-blue-50 text-blue-800 border-blue-200';
      break;
    case 'PERSONAL_OR_NAMED':
      label = 'Named contact';
      styleClasses = 'bg-indigo-50 text-indigo-800 border-indigo-200';
      break;
    case 'NO_REPLY':
      label = 'No-reply address';
      styleClasses = 'bg-slate-100 text-slate-700 border-slate-200';
      break;
    case 'UNKNOWN':
      label = 'Other';
      styleClasses = 'bg-slate-100 text-slate-600 border-slate-200';
      break;
  }

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold border ${styleClasses} ${className}`}
    >
      {label}
    </span>
  );
};

interface ValidationBadgeProps {
  status: EmailValidationStatus | string;
  className?: string;
}

export const ValidationBadge: React.FC<ValidationBadgeProps> = ({ status, className = '' }) => {
  let label = status as string;
  let styleClasses = 'bg-slate-100 text-slate-700 border-slate-200';

  switch (status) {
    case 'VALID':
      label = 'Format accepted';
      styleClasses = 'bg-emerald-50 text-emerald-800 border-emerald-200';
      break;
    case 'UNVERIFIED':
      label = 'Not independently verified';
      styleClasses = 'bg-amber-50 text-amber-800 border-amber-200';
      break;
    case 'INVALID':
      label = 'Rejected format';
      styleClasses = 'bg-rose-50 text-rose-800 border-rose-200';
      break;
  }

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold border ${styleClasses} ${className}`}
    >
      {label}
    </span>
  );
};
