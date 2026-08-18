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
      label = 'Role-based';
      styleClasses = 'bg-purple-50 text-purple-800 border-purple-200';
      break;
    case 'PERSONAL_OR_NAMED':
      label = 'Personal/Named';
      styleClasses = 'bg-emerald-50 text-emerald-800 border-emerald-200';
      break;
    case 'NO_REPLY':
      label = 'No-reply';
      styleClasses = 'bg-amber-50 text-amber-800 border-amber-200';
      break;
    case 'UNKNOWN':
      label = 'Unknown';
      styleClasses = 'bg-slate-100 text-slate-700 border-slate-200';
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
      label = 'Valid';
      styleClasses = 'bg-emerald-50 text-emerald-800 border-emerald-200';
      break;
    case 'UNVERIFIED':
      label = 'Unverified';
      styleClasses = 'bg-amber-50 text-amber-800 border-amber-200';
      break;
    case 'INVALID':
      label = 'Invalid';
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
